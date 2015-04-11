#!/usr/bin/env python
#coding:utf-8

import select
import socket
import struct
import time

CHUNCK_SIZE=10240

# 所有代理连接的socket数据：地址，套接字，未发送完的数据
peers = []	#(addr, fd, [])

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

def get_server_address():
	'''
		获取服务器IP+Port地址
	'''
	return ('127.0.0.1', 9527)
	sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
	sock.settimeout(10)
	try:
		sock.connect(('thickforest.github.io', 80))
		sock.send('GET /ip HTTP/1.0\r\nHost: thickforest.github.io\r\n\r\n')
		page = sock.recv(CHUNCK_SIZE)
		sock.close()
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
			try:
				fileno = _fd.fileno()
			except socket.error, e:
				# 当socket.recv 遇到 Connection timed out 或 Connection reset by peer的时候，会报Bad file descriptor,报错日志见(bug.txt:BUG1)
				fileno = -1
				print e
			print '将[(%s,%s), %d]移出peers' % (str(dest_addr), str(local_addr), fileno)
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
		
def sendto_dest(addr_tuple, data):
	'''
		给真正的对端发送数据
	'''
	global peers
	for _addr_tuple, fd, write_queue in peers:
		if addr_tuple == _addr_tuple:
			write_queue.append(data)
			return

	# 若之前未建立连接，连接之
	fd = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
	fd.setblocking(False)
	try:
		dest_addr, local_addr = addr_tuple
		fd.connect(dest_addr)
	except socket.error, e:
		print e
	# 待连接真正成功之时，再发送数据
	peers.append((addr_tuple, fd, [data]))
	

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
			tunnel_sock.close()
			time.sleep(5)
			continue

		# 清空隧道写队列
		tunnel_write_queue = []
		#peers.append((server_addr, tunnel_sock, tunnel_write_queue))

		# 清空隧道读缓存队列
		clear_tunnel_cache()

		while True:
			rfds = []
			wfds = []
			for x, fd, write_queue in peers:
				rfds.append(fd)
				if write_queue:
					wfds.append(fd)
			rfds.append(tunnel_sock)
			if tunnel_write_queue:
				wfds.append(tunnel_sock)

			#print 'select: %d %d' % (len(rfds), len(wfds))
			readable, writeable, exceptional = select.select(rfds, wfds, [], 30)

			tunnel_error = False	# 通道错误标志

			# 处理所有可读套接字
			for fd in readable:
				try:
					new_data = fd.recv(CHUNCK_SIZE)
				except socket.error, e:
					print e
					fd.close()
					new_data = ''
				if fd == tunnel_sock:
					#print '从隧道读取[%d]数据,开始送至真正对端写队列' % len(new_data)
					if not new_data:
						# Tunnel断开,重置
						print 'Tunnel读取失败，断开，重置'
						tunnel_error = True
						tunnel_sock.close()
						break
					process_tunnel_data(new_data)
				else:
					for (dest_addr, local_addr), _fd, x in peers:
						if fd == _fd:
							break
					#print '从真正对端[%s,%s]读取[%d]数据,开始送至隧道写队列' % (str(dest_addr), str(local_addr), len(new_data))
					if len(new_data) == 0:
						del_peer_by_fd(fd)
					dest_addr_hex = addr2hex(dest_addr)
					local_addr_hex = addr2hex(local_addr)
					data_len_hex = struct.pack('!I', len(new_data))
					# 将从真正的对端读取的数据,加上数据块头部信息，一并写入通道
					tunnel_write_queue.append(dest_addr_hex + local_addr_hex + data_len_hex + new_data)

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
						fd.send(write_queue.pop(0))
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

	# end while True:

