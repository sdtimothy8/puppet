#!/usr/bin/env python
#coding:utf-8

import select
import socket
import ssl
import struct
import time

CHUNCK_SIZE=10240
NEW = 1
DATA = 2
END = 3

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

def dns_resolver(name):
	# 本地域名解析
	try:
		import socket
        	results = socket.getaddrinfo(name,None)
		for family, socktype, proto, canonname, sockaddr in results:
			if len(sockaddr) == 2:
				return sockaddr[0]
	except socket.error, e:
		print '本地域名解析错误', e

	# google域名解析
	import os, socket, re
	seqid = os.urandom(2)
	host = ''.join(chr(len(x))+x for x in name.split('.'))
	data = '%s\x01\x00\x00\x01\x00\x00\x00\x00\x00\x00%s\x00\x00\x01\x00\x01' % (seqid, host)
	sock = socket.socket(socket.AF_INET,type=socket.SOCK_DGRAM)
	sock.settimeout(30)
	sock.sendto(data, ('8.8.8.8', 53))
	try:
		data = sock.recv(512)
	except socket.error, e:
		print '8.8.8.8域名解析错误', e
		return name
	iplist = ['.'.join(str(ord(x)) for x in s) for s in re.findall('\xc0.\x00\x01\x00\x01.{6}(.{4})', data) if all(ord(x) <= 255 for x in s)]
	if iplist:
		return iplist[0]
	print '域名解析失败'
	return name

def get_server_address():
	'''
		获取服务器IP+Port地址
	'''
	#return ('127.0.0.1', 9527)
	webip = dns_resolver('thickforest.github.io')
	sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
	sock = ssl.wrap_socket(sock)
	sock.settimeout(10)
	try:
		sock.connect((webip, 443))
		sock.send('GET /oray/home/ip2 HTTP/1.0\r\nHost: thickforest.github.io\r\n\r\n')
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
		if len(tunnel_data) < 18:	# 不足一个数据块头部信息
			return
		# 读取一个数据块头部信息
		head = tunnel_data[:18]
		data = tunnel_data[18:]
		dest_addr = hex2addr(head[:6])
		local_addr = hex2addr(head[6:12])
		status = struct.unpack('!H', head[12:14])[0]
		remain_len = struct.unpack('!I', head[14:])[0]
		current_addr_tuple = (dest_addr, local_addr)
		if status == END and remain_len == 0:
			# 数据块头部信息中的状态为END并且Len=0,表示关闭连接
			del_peer_by_addr(current_addr_tuple)
			current_addr_tuple = None
			tunnel_data = data
			process_tunnel_data('')
			return
		elif status == NEW and remain_len == 0:
			new_connection(current_addr_tuple)
			current_addr_tuple = None
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

def del_peer_by_fd_and_tell_tunnel(_fd):
	global peers
	found = False
	for peer in peers:
		(dest_addr, local_addr), fd, x = peer
		if fd == _fd:
			found = True
			try:
				fileno = _fd.fileno()
			except socket.error, e:
				# 当socket.recv 遇到 Connection timed out 或 Connection reset by peer的时候，会报Bad file descriptor,报错日志见(bug.txt:BUG1)
				fileno = -1
				print e
			print '将[%d, (%s,%s)]移出peers' % (fileno, str(dest_addr), str(local_addr))
			break
	if not found:
		print '[del_peer_by_fd_and_tell_tunnel] cannot find fd[%d] in peers' % _fd
		return
	fd.close()
	peers.remove(peer)

	# 通知Tunnel对方，此关闭连接
	dest_addr_hex = addr2hex(dest_addr)
	local_addr_hex = addr2hex(local_addr)
	status_hex = struct.pack('!H', END)
	zero_data_hex = struct.pack('!I', 0)
	print '通知Tunnel对方 关闭[%s,%s]' % (str(dest_addr), str(local_addr))
	tunnel_write_queue.append(dest_addr_hex + local_addr_hex + status_hex + zero_data_hex)

def del_peer_by_addr(addr_tuple):
	global peers
	found = False
	for peer in peers:
		_addr_tuple, fd, x = peer
		if _addr_tuple == addr_tuple:
			found = True
			(dest_addr, local_addr) = addr_tuple
			print '将[(%s,%s), %d]移出peers' % (str(dest_addr), str(local_addr), fd.fileno())
			break
	if not found:
		(dest_addr, local_addr) = addr_tuple
		print '[del_peer_by_addr] cannot find addr[%s,%s] in peers' % (str(dest_addr), str(local_addr))
		return
	fd.close()
	peers.remove(peer)
		
def sendto_dest(addr_tuple, data):
	'''
		给真正的对端发送数据
	'''
	global peers
	for _addr_tuple, fd, write_queue in peers:
		if addr_tuple == _addr_tuple:
			write_queue.append(data)
			return
	print '[sendto_dest] cannot find %s' % str(addr_tuple)

def new_connection(addr_tuple):
	'''
		连接真正的对端
	'''
	global peers
	fd = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
	fd.setblocking(False)
	try:
		dest_addr, local_addr = addr_tuple
		fd.connect(dest_addr)
	except socket.error, e:
		print e
	peers.append((addr_tuple, fd, []))
	

if __name__ == '__main__':
	while True:
		for x, fd, x in peers:
			fd.close()
		peers = []

		# 主动去获得服务端IP+Port
		server_addr = get_server_address()
		if not server_addr:
			print '主动获取服务端IP+Port失败，2min后重试'
			time.sleep(120)
			continue

		print '服务端：%s' % str(server_addr)

		# 主动去连接服务端
		tunnel_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
		tunnel_sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
		try:
			tunnel_sock.connect(server_addr)
		except socket.error, e:
			print '主动连接服务端失败，1min后重试'
			tunnel_sock.close()
			time.sleep(60)
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
						del_peer_by_fd_and_tell_tunnel(fd)
					dest_addr_hex = addr2hex(dest_addr)
					local_addr_hex = addr2hex(local_addr)
					status_hex = struct.pack('!H', DATA)
					data_len_hex = struct.pack('!I', len(new_data))
					# 将从真正的对端读取的数据,加上数据块头部信息，一并写入通道
					tunnel_write_queue.append(dest_addr_hex + local_addr_hex + status_hex + data_len_hex + new_data)

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
						del_peer_by_fd_and_tell_tunnel(fd)

			# Tunnel断裂，重置
			if tunnel_error:
				break

		# end while True:

	# end while True:

