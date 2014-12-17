from __future__ import absolute_import, division, print_function, with_statement

import time
import socket
import errno
import struct
import logging
import traceback
import random

from proxyx import eventloop, utils
from modules import prepull


TIMEOUTS_CLEAN_SIZE = 512
TIMEOUT_PRECISION = 4

MSG_FASTOPEN = 0x20000000

CMD_CONNECT = 1
CMD_BIND = 2
CMD_UDP_ASSOCIATE = 3






class TCPRelay(object):
	def __init__(self, config, dns_resolver, is_local):
		self._config = config
		self._is_local = is_local
		self._dns_resolver = dns_resolver
		self._closed = False
		self._eventloop = None
		self._fd_to_handlers = {}
		self._last_time = time.time()

		self._timeout = config['timeout']
		self._timeouts = [] # a list of all the handlers

		self._timeout_offset = 0 # last checked position for timeout
		self._handler_to_timeouts = {} # key:handler value: index in timeouts

		if is_local:
			listen_addr = config['local_address']
			listen_port = config['local_port']
		else:
			listen_addr = config['server_address']
			listen_port = config['server_port']
		self._listen_port = listen_port

		addrs = socket.getaddrinfo(listen_addr, listen_port, 0, socket.SOCK_STREAM, socket.SOL_TCP)

		if len(addrs) == 0:
			raise Exception("can't get addrinfo for %s:%d" %(listen_addr, listen_port))
		af, socktype, proto, canonname, sa = addrs[0]
		server_socket = socket.socket(af, socktype, proto)
		server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
		server_socket.bind(sa)
		server_socket.setblocking(False)
		if config['fast_open']:
			try:
				server_socket.setsockopt(socket.SOL_TCP, 23, 5)
			except socket.error:
				logging.error('warning: fast open is not available')
				self._config['fast_open'] = False
		server_socket.listen(1024)
		self._server_socket = server_socket

	# add listen sock's handler
	def add_to_loop(self, loop):
		if self._eventloop:
			raise Exception('already add to loop')
		if self._closed:
			raise Exception('already closed')
		self._eventloop = loop
		loop.add_handler(self._handle_events)

		self._eventloop.add(self._server_socket, eventloop.POLL_IN | eventloop.POLL_ERR)

	def remove_hanlder(self, handler):
		index = self._handler_to_timeouts.get(hash(handler), -1)
		if index >= 0:
			#delete is O(n), so we just set it to None
			self._timeouts[index] = None
			del self._handler_to_timeouts[hash(handler)]

	def update_activity(self, handler):
		now = int(time.time())
		if now - handler.last_activity < TIMEOUT_PRECISION:
			return
		handler.last_activity = now
		index = self._handler_to_timeouts.get(hash(handler), -1)
		if index >= 0:
			self._timeouts[index] = None
		length = len(self._timeouts)
		self._timeouts.append(handler)
		self._handler_to_timeouts[hash(handler)] = length

	def _sweep_timeout(self):
		if self._timeout:
			logging.log(utils.VERBOSE_LEVEL, 'sweeping timeouts')
			now = time.time()
			length = len(self._timeouts)
			pos = self._timeout_offset
			handler = None
			logging.debug('pos:%d length:%d', pos, length)
			while pos < length:
				handler = self._timeouts[pos]

				if handler:

					if now - handler.last_activity < self._timeout:
						break
					else:
						if handler.remote_address:
							logging.warn('timed out:%s :%d' % handler.remote_address)
						else:
							logging.warn('timed out')
						handler.destroy()
						self._timeouts[pos] = None # free memory
						pos += 1
				else:
					pos += 1

			if pos > TIMEOUTS_CLEAN_SIZE and pos > length >> 1:

				self._timeouts = self._timeouts[pos:]
				for key in self._handler_to_timeouts:
					self._handler_to_timeouts[key] = pos
				pos = 0
			self._timeout_offset = pos

	def _handle_events(self, events):
		for sock, fd, event in events:
			if sock:
				#logging.debug("TCPRelay _handle_events:[fd:%d], [event:%d]", fd, event)
				logging.log(utils.VERBOSE_LEVEL, 'fd %d %s', fd, eventloop.EVENT_NAMES.get(event, event))
			if sock == self._server_socket:
				if event & eventloop.POLL_ERR:
					raise Exception('server_socket error')
				try:
					logging.debug('accept')
					conn = self._server_socket.accept()
					prepull.TCPRelayHandler(self, self._fd_to_handlers,
									self._eventloop, conn[0], self._config,
									self._dns_resolver, self._is_local)
				except (OSError, IOError) as e:
					error_no = eventloop.errno_from_exception(e)
					if error_no in (errno.EAGAIN, errno.EINPROGRESS, errno.EWOULDBLOCK):
						continue
					else:
						logging.error(e)
						if self._config['verbose']:
							traceback.print_exc()
			else:
				if sock:
					handler = self._fd_to_handlers.get(fd, None)
					if handler:
						handler.handle_event(sock, event)
				else:
					logging.warn('poll removed fd')
		now = time.time()
		if now - self._last_time > TIMEOUT_PRECISION:
			self._sweep_timeout()
			self._last_time = now
		if self._closed:
			if self._server_socket:
				self._eventloop.remove(self._server_socket)
				self._server_socket.close()
				self._server_socket = None
				logging.info('closed listen port %d', self._listen_port)
			if not self._fd_to_handlers:
				self._eventloop.remove_hanlder(self._handler_events)

	def close(self, next_tick = False):
		self._closed = True
		if not next_tick:
			self._server_socket.close()




