#!/usr/bin/env python
#coding:utf-8

import select
import socket
import struct
import time

CHUNCK_SIZE=1024

# 所有socket数据：地址，套接字，未发送完的数据
peers = []	#(addr, fd, [])

def get_server_address():
	'''
		获取服务器IP+Port地址
	'''
	sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
	sock.settimeout(10)
	try:
		sock.connect(('thickforest.github.io', 80))
		sock.send('GET /ip HTTP/1.0\r\nHost: thickforest.github.io\r\n\r\n')
		page = sock.recv(CHUNCK_SIZE)
	except socket.error, e:
		return None
	last_line = ''
	for line in page.split():
		if line.strip() != 0:
			last_line = line
	_l = last_line.split(':')
	if len(_l) !=2:
		return None
	ip, port = _l
	try:
		return (ip, int(port))
	except ValueError:
		return None


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
		return


def del_peer_by_fd(_fd):
	global peers
	for peer in peers:
		addr, fd, x = peer
		if fd == _fd:
			print '将[%s]移出peers' % str(addr)
			break
	fd.close()
	peers.remove(peer)

def del_peer_by_addr(addr):
	global peers
	for peer in peers:
		_addr, fd, x = peer
		if _addr== addr:
			break
	fd.close()
	peers.remove(peer)
	
#def sendto_tunnel(data):
#	global peers
#	global tunnel_sock
#	for x, fd, write_queue in peers:
#		if fd == tunnel_sock:
#			write_queue.append(data)
		
def sendto_dest(addr, data):
	'''
		给真正的对端发送数据
	'''
	global peers
	for _addr, fd, write_queue in peers:
		if addr == _addr:
			write_queue.append(data)
			return

	# 若之前未建立连接，连接之
	fd = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
	fd.setblocking(False)
	try:
		fd.connect(addr)
	except socket.error, e:
		print e
	# 待连接真正成功之时，再发送数据
	peers.append((addr, fd, [data]))
	

if __name__ == '__main__':
	while True:
		for x, fd, x in peers:
			fd.close()
		peers = []

		# 主动去获得服务端IP+Port
		server_addr = get_server_address()
		if not server_addr:
			print '主动获取服务端IP+Port失败，5s后重试'
			time.sleep(5)
			continue

		print '服务端：%s' % str(server_addr)

		# 主动去连接服务端
		tunnel_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
		try:
			tunnel_sock.connect(server_addr)
		except socket.error, e:
			print '主动连接服务端失败，5s后重试'
			time.sleep(5)
			continue

		# 清空隧道写队列
		tunnel_write_queue = []
		peers.append((server_addr, tunnel_sock, tunnel_write_queue))

		# 清空隧道读缓存队列
		clear_tunnel_cache()

		while True:
			fds = []
			for x, fd, x in peers:
				fds.append(fd)

			readable, writeable, exceptional = select.select(fds, fds, [], 10)

			tunnel_error = False	# 通道错误标志
			data_travaling = False	# 检测是否有数据传输

			# 处理所有可读套接字
			for fd in readable:
				data_travaling = True
				new_data = fd.recv(CHUNCK_SIZE)
				if fd == tunnel_sock:
					print '从隧道读取[%d]数据,开始送至真正对端写队列' % len(new_data)
					if not new_data:
						# Tunnel断开,重置
						print 'Tunnel读取失败，断开，重置'
						tunnel_error = True
						break
					process_tunnel_data(new_data)
				else:
					for addr, _fd, x in peers:
						if fd == _fd:
							break
					print '从真正对端[%s]读取[%d]数据,开始送至隧道写队列' % (str(addr), len(new_data))
					ip, port = addr
					a,b,c,d = map(int, ip.split('.'))
					head = struct.pack('!4BHI', a, b, c, d, port, len(new_data))
					# 将从真正的对端读取的数据,加上数据块头部信息，一并写入通道
					tunnel_write_queue.append(head + new_data)

			# 处理所有可写套接字
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
						# (将数据写入真正的对端) 或 (将数据块写入通道)
						data = write_queue.pop(0)
						fd.send(data)
					except socket.error, e:
						if fd == tunnel_sock:
							# 若Tunnel无法写入，则断开Tunnel,重置
							print 'Tunnel写失败，断开，重置'
							tunnel_error = True
							break

						print '往真正对端写失败'
						del_peer_by_fd(fd)

						# 通知Tunnel对方，此关闭连接
						ip, port = addr
						a,b,c,d = map(int, ip.split('.'))
						close_data = struct.pack('!4BHI', a, b, c, d, port, 0)
						print '通知Tunnel对方 关闭[%s]' % str(addr)
						tunnel_write_queue.append(close_data)

			# Tunnel断裂，重置
			if tunnel_error:
				break

			if not data_travaling:
				print '没有数据传输，歇5s'
				time.sleep(5)
