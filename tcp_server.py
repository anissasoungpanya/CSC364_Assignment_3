"""
TCP-like Server over UDP
Implements packet loss simulation, RTT simulation, checksum validation, and cumulative ACKs
"""

import socket
import struct
import random
import time
import threading
import sys

SEQ_NUM_SIZE = 4  
CHECKSUM_SIZE = 2  
ACK_SIZE = 4  
CHUNK_SIZE = 1024  

RTT_DELAY = 0.1 
DEFAULT_LOSS_PROB = 0.1 

class TCPServer:
    def __init__(self, host='localhost', port=8888, loss_prob=DEFAULT_LOSS_PROB):
        self.host = host
        self.port = port
        self.loss_prob = loss_prob
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind((host, port))
        
        self.expected_seq = 0  
        self.received_data = {}  
        self.output_buffer = [] 
        
        self.ack_lock = threading.Lock()
        self.ack_timer_active = False
        self.ack_timer_thread = None
        self.client_addr = None
        
        self.total_packets_received = 0
        self.total_packets_dropped = 0
        self.total_checksum_errors = 0
        
    def calculate_checksum(self, data):
        if not data:
            return 0
        checksum = sum(data) % 65535
        return checksum
    
    def verify_checksum(self, data, received_checksum):
        calculated_checksum = self.calculate_checksum(data)
        return calculated_checksum == received_checksum
    
    def send_ack(self, client_addr, ack_num):
        ack_packet = struct.pack('!I', ack_num)
        self.sock.sendto(ack_packet, client_addr)
    
    def send_ack_after_rtt(self):
        time.sleep(RTT_DELAY)
        with self.ack_lock:
            if self.ack_timer_active:
                self.send_ack(self.client_addr, self.expected_seq)
                self.ack_timer_active = False
    
    def process_packet(self, packet_data, client_addr):
        if len(packet_data) < SEQ_NUM_SIZE + CHECKSUM_SIZE:
            return  
        
        seq_num = struct.unpack('!I', packet_data[:SEQ_NUM_SIZE])[0]
        checksum = struct.unpack('!H', packet_data[SEQ_NUM_SIZE:SEQ_NUM_SIZE+CHECKSUM_SIZE])[0]
        data = packet_data[SEQ_NUM_SIZE + CHECKSUM_SIZE:]
        
        if random.random() < self.loss_prob:
            self.total_packets_dropped += 1
            return 
        
        self.total_packets_received += 1
        
        if not self.verify_checksum(data, checksum):
            self.total_checksum_errors += 1
            return 
        
        if self.client_addr is None:
            self.client_addr = client_addr
        
        if seq_num < self.expected_seq:
            # Duplicate packet, send duplicate ACK
            self.send_ack(client_addr, self.expected_seq)
            return
        
        is_new_packet = seq_num not in self.received_data
        
        if is_new_packet:
            self.received_data[seq_num] = data

            with self.ack_lock:
                if not self.ack_timer_active:
                    self.ack_timer_active = True
                    self.ack_timer_thread = threading.Thread(target=self.send_ack_after_rtt, daemon=True)
                    self.ack_timer_thread.start()
        
        while self.expected_seq in self.received_data:
            self.output_buffer.append(self.received_data.pop(self.expected_seq))
            self.expected_seq += len(self.output_buffer[-1])
    
    def run(self):
        client_addr = None
        last_packet_time = None
        completion_timeout = 30.0 
        
        self.sock.settimeout(2.0) 
        
        while True:
            try:
                packet_data, addr = self.sock.recvfrom(65507)  
                
                if client_addr is None:
                    client_addr = addr
                    print(f"Client connected from {addr}")
                
                self.process_packet(packet_data, client_addr)
                last_packet_time = time.time()
                
            except socket.timeout:
                if last_packet_time and (time.time() - last_packet_time) > completion_timeout:
                    if self.expected_seq > 0:
                        if len(self.output_buffer) > 0:
                            break 
                continue
            except Exception as e:
                print(f"Error: {e}")
                continue
        
        if self.output_buffer:
            output_file = 'received.txt'
            with open(output_file, 'wb') as f:
                for chunk in self.output_buffer:
                    f.write(chunk)
            
            total_bytes = sum(len(chunk) for chunk in self.output_buffer)
            print(f"\nFile transfer complete")
            print(f"Total bytes received: {total_bytes}")
        
        self.sock.close()

def main():
    loss_prob = DEFAULT_LOSS_PROB
    if len(sys.argv) > 1:
        try:
            loss_prob = float(sys.argv[1]) / 100.0 
        except ValueError:
            print(f"Invalid loss probability: {sys.argv[1]}")
            loss_prob = DEFAULT_LOSS_PROB
    
    server = TCPServer(loss_prob=loss_prob)
    server.run()

if __name__ == '__main__':
    main()

