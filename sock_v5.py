#!/usr/bin/env python
#coding:utf-8
import struct

INIT_STATE = 0
TEST_DOWN = 1
DEST_CONFIRM = 2

socks = []	# [local_addr, fd, dest_addr, state]


def isUnEstablishedSockV5(fd):
	global socks
	for sock in socks:
		if sock[1] == fd:
			return True
	return False
	

def addUnEstablishedSockV5(addr, fd):
	global socks
	socks.append([addr, fd, None, INIT_STATE])


def makeSockV5Connection(fd):
	global socks
	found = False
	for sock in socks:
		addr, _fd, x, state = sock
		if fd == _fd:
			found = True
			break
	if not found:
		return None

	data = fd.recv(1024)
	if state == INIT_STATE:
		fd.send(struct.pack('2B', 5, 0))
		sock[3] = TEST_DOWN
		return None
	elif state == TEST_DOWN:
		a,b,c,d,port = struct.unpack('!4BH',data[4:])
		ip = '%d.%d.%d.%d' % (a,b,c,d)
		dest_addr = (ip, port)
		sock[2] = dest_addr
		sock[3] = DEST_CONFIRM
		socks.remove(sock)

		ip, port = addr
		a,b,c,d = map(int, ip.split('.'))
		fd.send(struct.pack('!8BH',5,0,0,1,a,b,c,d,port))
		return dest_addr


def unEstablishedSocks():
	global socks
	fds = []
	for x, fd, x, x in socks:
		fds.append(fd)
	return fds

