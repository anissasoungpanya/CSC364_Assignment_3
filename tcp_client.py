"""
TCP-like Client over UDP
Implements slow start, congestion avoidance, fast retransmit, timeout handling
"""

import socket
import struct
import time
import threading
import sys
import os

SEQ_NUM_SIZE = 4  
CHECKSUM_SIZE = 2  
ACK_SIZE = 4 
CHUNK_SIZE = 1024  

TIMEOUT = 0.5  
INITIAL_CWND = 1  
INITIAL_SSTHRESH = 64  
FAST_RETRANSMIT_DUP_ACKS = 3  

class TCPClient:
    def __init__(self, server_host='localhost', server_port=8888):
        self.server_host = server_host
        self.server_port = server_port
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.settimeout(TIMEOUT)
        
        self.file_data = None
        self.total_chunks = 0
        self.chunks = [] 
        
        self.cwnd = INITIAL_CWND  
        self.ssthresh = INITIAL_SSTHRESH 
        self.state = 'slow_start' 
        
        self.next_seq_to_send = 0  
        self.last_acked_seq = -1  
        self.unacked_packets = {}  # {seq_num: (packet_data, send_time, retransmit_count)}
        self.last_ack_received = -1 
        self.duplicate_ack_count = 0 

        self.rtt_samples = []  
        self.current_rtt = 0.1 
        
        self.metrics = {
            'cwnd_history': [],  # [(time, cwnd), ...]
            'retransmission_history': [],  # [(time, count), ...]
            'start_time': None
        }
        
        self.total_retransmissions = 0
        self.timeout_retransmissions = 0
        self.fast_retransmissions = 0
        
        self.lock = threading.Lock()
        
    def calculate_checksum(self, data):
        if not data:
            return 0
        checksum = sum(data) % 65535
        return checksum
    
    def create_packet(self, seq_num, data):
        checksum = self.calculate_checksum(data)
        packet = struct.pack('!I', seq_num)  
        packet += struct.pack('!H', checksum)  
        packet += data  
        return packet
    
    def read_file(self, filename):
        try:
            with open(filename, 'rb') as f:
                self.file_data = f.read()
            
            self.chunks = []
            seq_num = 0
            for i in range(0, len(self.file_data), CHUNK_SIZE):
                chunk_data = self.file_data[i:i+CHUNK_SIZE]
                self.chunks.append((seq_num, chunk_data))
                seq_num += len(chunk_data)
            
            self.total_chunks = len(self.chunks)
            print(f"File read: {len(self.file_data)} bytes, {self.total_chunks} chunks")
            return True
        except Exception as e:
            print(f"Error reading file: {e}")
            return False
    
    def can_send_more(self):
        with self.lock:
            in_flight_count = 0
            for seq in self.unacked_packets.keys():
                packet_size = None
                for s, chunk_data in self.chunks:
                    if s == seq:
                        packet_size = len(chunk_data)
                        break
                if packet_size:
                    if seq + packet_size > self.last_acked_seq + 1:
                        in_flight_count += 1
            
            return in_flight_count < int(self.cwnd)
    
    def send_packet(self, seq_num, data, is_retransmit=False):
        packet = self.create_packet(seq_num, data)
        self.sock.sendto(packet, (self.server_host, self.server_port))
        
        send_time = time.time()
        if seq_num not in self.unacked_packets:
            self.unacked_packets[seq_num] = (packet, send_time, 0)
            if is_retransmit:
                self.total_retransmissions += 1
                self.timeout_retransmissions += 1
                with self.lock:
                    self.metrics['retransmission_history'].append((
                        time.time() - self.metrics['start_time'],
                        self.total_retransmissions
                    ))
        else:
            old_packet, old_time, retransmit_count = self.unacked_packets[seq_num]
            self.unacked_packets[seq_num] = (packet, send_time, retransmit_count + 1)
            self.total_retransmissions += 1
            if is_retransmit:
                self.timeout_retransmissions += 1
            else:
                self.fast_retransmissions += 1
            with self.lock:
                self.metrics['retransmission_history'].append((
                    time.time() - self.metrics['start_time'],
                    self.total_retransmissions
                ))
    
    def handle_ack(self, ack_num):
        with self.lock:
            if ack_num == self.last_ack_received:
                self.duplicate_ack_count += 1
                
                if self.duplicate_ack_count == FAST_RETRANSMIT_DUP_ACKS:
                    # Fast retransmit
                    retransmit_seq = ack_num
                    
                    chunk_data = None
                    for seq, data in self.chunks:
                        if seq == retransmit_seq:
                            chunk_data = data
                            break
                    
                    if chunk_data is not None:
                        packet = self.create_packet(retransmit_seq, chunk_data)
                        self.sock.sendto(packet, (self.server_host, self.server_port))
                        
                        send_time = time.time()
                        if retransmit_seq in self.unacked_packets:
                            old_packet, old_time, retransmit_count = self.unacked_packets[retransmit_seq]
                            self.unacked_packets[retransmit_seq] = (packet, send_time, retransmit_count + 1)
                        else:
                            self.unacked_packets[retransmit_seq] = (packet, send_time, 1)
                        
                        self.total_retransmissions += 1
                        self.fast_retransmissions += 1
                        self.metrics['retransmission_history'].append((
                            time.time() - self.metrics['start_time'],
                            self.total_retransmissions
                        ))
                        
                        self.ssthresh = max(int(self.cwnd / 2), 2)
                        self.cwnd = self.ssthresh + 3  
                        self.state = 'congestion_avoidance'
                        
                        self.duplicate_ack_count = 0
                
                return 
            
            # New ACK 
            if ack_num > self.last_ack_received:
                self.duplicate_ack_count = 0
                self.last_ack_received = ack_num
                
                acked_packets = []
                for seq in list(self.unacked_packets.keys()):
                    packet_size = None
                    for s, chunk_data in self.chunks:
                        if s == seq:
                            packet_size = len(chunk_data)
                            break
                    
                    if packet_size and seq + packet_size <= ack_num:
                        acked_packets.append(seq)
                
                for seq in acked_packets:
                    packet_data, send_time, retransmit_count = self.unacked_packets.pop(seq, (None, None, None))
                    if packet_data and send_time:
                        rtt = time.time() - send_time
                        self.rtt_samples.append(rtt)
                        if len(self.rtt_samples) == 1:
                            self.current_rtt = rtt
                        else:
                            self.current_rtt = 0.875 * self.current_rtt + 0.125 * rtt
                
                self.last_acked_seq = max(self.last_acked_seq, ack_num - 1)
                
                packets_acked = len(acked_packets)
                
                if self.state == 'slow_start':
                    self.cwnd += packets_acked
                    if self.cwnd >= self.ssthresh:
                        self.state = 'congestion_avoidance'
                else:  # congestion_avoidance
                    self.cwnd += packets_acked / self.cwnd
                    self.cwnd = int(self.cwnd) if self.cwnd >= 1 else 1
                
                current_time = time.time() - self.metrics['start_time']
                rtt_number = int(current_time / self.current_rtt) if self.current_rtt > 0 else 0
                self.metrics['cwnd_history'].append((rtt_number, int(self.cwnd)))
    
    def check_timeouts(self):
        current_time = time.time()
        timed_out_packets = []
        
        for seq_num, (packet_data, send_time, retransmit_count) in list(self.unacked_packets.items()):
            elapsed = current_time - send_time
            if elapsed > TIMEOUT:
                timed_out_packets.append(seq_num)
        
        for seq_num in timed_out_packets:
            for seq, chunk_data in self.chunks:
                if seq == seq_num:
                    print(f"Timeout: retransmitting seq={seq_num}")
                    self.ssthresh = max(int(self.cwnd / 2), 2)
                    self.cwnd = INITIAL_CWND
                    self.state = 'slow_start'
                    self.send_packet(seq_num, chunk_data, is_retransmit=True)
                    break
    
    def receiver_thread(self):
        while True:
            try:
                data, addr = self.sock.recvfrom(ACK_SIZE)
                if len(data) >= ACK_SIZE:
                    ack_num = struct.unpack('!I', data)[0]
                    self.handle_ack(ack_num)
            except socket.timeout:
                self.check_timeouts()
                continue
            except Exception as e:
                if self.last_acked_seq >= self.chunks[-1][0] + len(self.chunks[-1][1]) - 1:
                    break 
                continue
    
    def transfer_file(self, filename):
        if not self.read_file(filename):
            return False
        
        self.metrics['start_time'] = time.time()

        receiver = threading.Thread(target=self.receiver_thread, daemon=True)
        receiver.start()
        
        chunk_index = 0
        while chunk_index < self.total_chunks:
            while self.can_send_more() and chunk_index < self.total_chunks:
                seq_num, chunk_data = self.chunks[chunk_index]
                self.send_packet(seq_num, chunk_data)
                chunk_index += 1
            
            time.sleep(0.01)
            
            self.check_timeouts()
        
        max_wait_time = 60  
        start_wait = time.time()
        last_ack_time = None 
        
        while len(self.unacked_packets) > 0:
            current_time = time.time()
            
            with self.lock:
                if self.last_ack_received > -1:
                    if last_ack_time is None:
                        last_ack_time = current_time
                    else:
                        last_ack_time = current_time
            
            time.sleep(0.1)
            self.check_timeouts()
        
        time.sleep(1.0)
        
        print(f"\nTransfer complete")
        
        return True
    
    def save_metrics(self, filename_prefix='metrics'):
        cwnd_file = f'{filename_prefix}_cwnd.txt'
        with open(cwnd_file, 'w') as f:
            f.write("RTT,cwnd\n")
            for rtt, cwnd in self.metrics['cwnd_history']:
                f.write(f"{rtt},{cwnd}\n")
        
        retrans_file = f'{filename_prefix}_retransmissions.txt'
        with open(retrans_file, 'w') as f:
            f.write("time,retransmissions\n")
            for t, count in self.metrics['retransmission_history']:
                f.write(f"{t},{count}\n")
        

def main():
    
    filename = sys.argv[1]
    server_host = sys.argv[2] if len(sys.argv) > 2 else 'localhost'
    server_port = int(sys.argv[3]) if len(sys.argv) > 3 else 8888
    
    loss_prob = 10  
    if 'loss' in filename.lower():
        pass
    
    client = TCPClient(server_host, server_port)
    success = client.transfer_file(filename)
    
    if success:
        loss_suffix = os.environ.get('LOSS_PERCENT', '10')
        client.save_metrics(f'metrics_loss_{loss_suffix}')
    
    return 0 if success else 1

if __name__ == '__main__':
    sys.exit(main())

