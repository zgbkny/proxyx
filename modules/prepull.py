from __future__ import absolute_import, division, print_function, with_statement

import sys
import os

import time
import socket
import errno
import struct
import logging
import traceback
import random

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../'))
from proxyx import eventloop, utils
from modules import httpx

TIMEOUTS_CLEAN_SIZE = 512
TIMEOUT_PRECISION = 4

MSG_FASTOPEN = 0x20000000

CMD_CONNECT = 1
CMD_BIND = 2
CMD_UDP_ASSOCIATE = 3
# local:
# stage 0 init
# stage 1 hello received, hello sent
# stage 2 UDP assoc
# stage 3 DNS
# stage 4 addr received, reply sent
# stage 5 remote connected

# remote:
# stage 0 init
# stage 3 DNS
# stage 4 addr received, reply sent
# stage 5 remote connected

STAGE_INIT = 0
STAGE_HEADER = 1
STAGE_RESPONSE_INIT = 2
STAGE_DESTROYED = 3


# stream direction
STREAM_UP = 0
STREAM_DOWN = 1

# stream wait status
WAIT_STATUS_INIT = 0
WAIT_STATUS_READING = 1
WAIT_STATUS_WRITING = 2
WAIT_STATUS_READWRITING = WAIT_STATUS_READING | WAIT_STATUS_WRITING

BUF_SIZE = 64 * 1024


def parse_header(data):
	host = ''
	if data.find('http://') != -1:
		start = data.find('http://') + 7
		host = data[start:data.find('/', start)]

	elif data.find('Host:') != -1:
		start = data.find('Host:') + 6
		host = data[start:data.find('\r\n', start)]

	else:
		return None
	logging.debug('data:%s', data)
	logging.debug('host:%s', host)
	return host, 80

class TCPRelayHandler(object):
	def __init__(self, server, fd_to_handlers, loop, local_sock, config, dns_resolver, is_local):
                self._ttfb = time.time()
		self._request = httpx.HTTPX()
		self._response = httpx.HTTPX()
		self._server = server
		self._fd_to_handlers = fd_to_handlers
		self._loop = loop
		self._local_sock = local_sock
		self._remote_sock = None
		self._config = config
		self._dns_resolver = dns_resolver
		self._is_local = is_local
		self._stage = STAGE_INIT
		self._fastopen_connected = False
		self._data_to_write_to_local = []
		self._data_to_write_to_remote = []
		self._upstream_status = WAIT_STATUS_READING
		self._downstream_status = WAIT_STATUS_INIT
		self._remote_address = None
		if is_local:
			self._chosen_server = self._get_a_server()
		fd_to_handlers[local_sock.fileno()] = self
		local_sock.setblocking(False)
		local_sock.setsockopt(socket.SOL_TCP, socket.TCP_NODELAY, 1)
		loop.add(local_sock, eventloop.POLL_IN | eventloop.POLL_ERR)
		self.last_activity = 0
		self._update_activity()

	def _get_a_server(self):
		server_address = self._config['server_address']
		server_port = self._config['server_port']
		return server_address, server_port

	def _update_activity(self):
		self._server.update_activity(self)

	def _create_remote_sock(self, ip, port):
		addrs = socket.getaddrinfo(ip, port, 0, socket.SOCK_STREAM, socket.SOL_TCP)

		if len(addrs) == 0:
			raise Exception("getaddrinfo failed for %s:%d" % (ip, port))
		af, socktype, proto, canonname, sa = addrs[0]
		remote_sock = socket.socket(af, socktype, proto)
		self._remote_sock = remote_sock
		self._fd_to_handlers[remote_sock.fileno()] = self
		remote_sock.setblocking(False)
		remote_sock.setsockopt(socket.SOL_TCP, socket.TCP_NODELAY, 1)
		return remote_sock

	def _update_stream(self, stream, status):
		dirty = False
		if stream == STREAM_DOWN:
			if self._downstream_status != status:
				self._downstream_status = status
				dirty = True
		elif stream == STREAM_UP:
			if self._upstream_status != status:
				self._upstream_status = status
				dirty = True
		if dirty:
			if self._local_sock:
				event = eventloop.POLL_ERR
				if self._downstream_status & WAIT_STATUS_WRITING:
					event |= eventloop.POLL_OUT
				if self._upstream_status &WAIT_STATUS_READING:
					event |= eventloop.POLL_IN
				self._loop.modify(self._local_sock, event)
			if self._remote_sock:
				event = eventloop.POLL_ERR
				if self._downstream_status & WAIT_STATUS_READING:
					event |= eventloop.POLL_IN
				if self._upstream_status & WAIT_STATUS_WRITING:
					event |= eventloop.POLL_OUT
				self._loop.modify(self._remote_sock, event)


	def _handle_dns_resolved(self, result, error):
		logging.debug('_handle_dns_resolved')
		logging.info('dns_resolver:%s', result)
		if error:
			logging.error(error)
			self.destroy()
			return
		if result:
			ip = result
			if ip:
				try:
					remote_addr = ip
					if self._is_local:
						remote_port = self._chosen_server[1]
					else:
						remote_port = self._remote_address[1]

					logging.debug('%s,%d', remote_addr, remote_port)
					remote_sock = self._create_remote_sock(remote_addr, remote_port)
					try:
						remote_sock.connect((remote_addr, remote_port))
					except (OSError, IOError) as e:
						if eventloop.errno_from_exception(e) == errno.EINPROGRESS:
							logging.debug('EINPROGRESS')
						logging.debug(e)
					logging.debug('connect wait')
					self._loop.add(remote_sock, eventloop.POLL_ERR | eventloop.POLL_OUT)
					self._update_stream(STREAM_UP, WAIT_STATUS_READWRITING)
					self._update_stream(STREAM_DOWN, WAIT_STATUS_READING)

					return

				except (OSError, IOError) as e:
					logging.error(e)
					if self._config['verbose']:
						traceback.print_exc()
		self.destroy()

	def _handler(self):
		logging.debug('_handler')
		if self._remote_sock:
			pass
		else:
			remote_address, remote_port = self._request.get_host_address_and_port()
			logging.debug('handler:%s, %d', remote_address, remote_port)

			self._remote_address = (remote_address, remote_port)
			self._dns_resolver.resolve(self._remote_address[0], self._handle_dns_resolved)

	def _write_to_sock(self, data, sock):
		if  not data or not sock:
			return False
		uncomplete = False
		try:
			l = len(data)
			s = sock.send(data)
			#logging.info('>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>data:%d', l-s)
			if s < l:
				data = data[s:]
				uncomplete = True
		except (OSError, IOError) as e:
			error_no = eventloop.errno_from_exception(e)
			if error_no in (errno.EAGAIN, errno.EINPROGRESS,
					errno.EWOULDBLOCK):
				uncomplete = True
			else:
				logging.error(e)
			if self._config['verbose']:
				traceback.print_exc()
			self.destroy()
			return False
		if uncomplete:
			if sock == self._local_sock:
				self._data_to_write_to_local.append(data)
				self._update_stream(STREAM_DOWN, WAIT_STATUS_WRITING)
			elif sock == self._remote_sock:
				self._data_to_write_to_remote.append(data)
				self._update_stream(STREAM_UP, WAIT_STATUS_WRITING)
			else:
				logging.error('write_all_to_sock:unknown socket')
		else:
			if sock == self._local_sock:
				self._update_stream(STREAM_DOWN, WAIT_STATUS_READING)
			elif sock == self._remote_sock:
				self._update_stream(STREAM_UP, WAIT_STATUS_READING)
			else:
				logging.error('write_all_to_sock:unknown socket')
		return True

	# message from upstream
	def _on_remote_read(self):
		logging.debug('_on_remote_read')
		self._update_activity()
		if not self._remote_sock:
			return
		is_local = self._is_local
		data = None
		try:
			data = self._remote_sock.recv(BUF_SIZE)
		except (OSError, IOError) as e:
			if eventloop.errno_from_exception(e) in (errno.ETIMEDOUT, errno.EAGAIN, errno.EWOULDBLOCK):
				return
		if not data:
                        
			self.destroy()
			return
                if time.time() - self._ttfb >= 1:
                        logging.info('---------------------------------ttfb:%d', time.time() - self._ttfb)
                        logging.info(self._host)

		if self._stage == STAGE_HEADER:
			self._stage = STAGE_RESPONSE_INIT
		self._data_to_write_to_local.append(data)
		self._on_local_write()
		

	def _on_remote_write(self):
		logging.debug('_on_remote_write')
		#logging.info('begin:%s, %d', self._remote_address, time.time() - self._ttfb)
		#self._ttfb = time.time()
		self._update_activity()
		if self._stage == STAGE_INIT:
			self._stage = STAGE_HEADER

		if self._data_to_write_to_remote:
			data = b''.join(self._data_to_write_to_remote)
			self._data_to_write_to_remote = []
			self._write_to_sock(data, self._remote_sock)
		else:
			self._update_stream(STREAM_UP, WAIT_STATUS_READING)

	def _on_remote_error(self):
		logging.debug('_on_remote_error')
		self._update_activity()
		self.destroy()

	# message from downstream

	def _on_local_read(self):
		logging.debug('_on_local_read')
		self._update_activity()
		if not self._local_sock:
			return
		is_local = self._is_local
		data = None
		try:
			data = self._local_sock.recv(BUF_SIZE)
		except (OSError, IOError) as e:
			if eventloop.errno_from_exception(e) in (errno.ETIMEDOUT, errno.EAGAIN, errno.EWOULDBLOCK):
				return
		if not data:
			self.destroy()
			return
                
		if self._stage == STAGE_INIT:
			header_result = parse_header(data)
			if header_result is None:
				raise Exception('can not parse header')
			self._host = header_result
			remote_addr, remote_port = header_result
			#self._remote_address = remote_addr, remote_port
			self._data_to_write_to_remote.append(data)
			#self._dns_resolver.resolve(self._remote_address[0], self._handle_dns_resolved)
			logging.info(remote_addr)
			addresses = socket.getaddrinfo(remote_addr, remote_port, 0, 0, socket.SOL_TCP)
			af, socktype, proto, canonname, sa = addresses[0]
			self._remote_address = sa
			self._handle_dns_resolved(sa[0], None)

		elif self._stage == STAGE_HEADER:
			self._data_to_write_to_remote.append(data)
			self._on_remote_write()

	def _on_local_read_back(self):
		logging.debug('_on_local_read')
		self._update_activity()
		if not self._local_sock:
			return
		is_local = self._is_local
		data = None
		try:
			data = self._local_sock.recv(BUF_SIZE)
		except (OSError, IOError) as e:
			if eventloop.errno_from_exception(e) in (errno.ETIMEDOUT, errno.EAGAIN, errno.EWOULDBLOCK):
				return
		if not data:
			self.destroy()
			return
		logging.debug('data:%s', data[0:80])
		if self._request.check_header() == False:
			logging.debug('parse')
			if self._request.parse_request(data) == False:
				self.destroy()
			if self._request.check_header() == True:
				
				self._handler()
				logging.debug('send after parse')
				
		else:
			self._data_to_write_to_remote.append(data)
			logging.debug('send')
			self._on_remote_write()
		

	def _on_local_write(self):
		logging.debug('_on_local_write')
		self._update_activity()
		if self._data_to_write_to_local:
			data = b''.join(self._data_to_write_to_local)
			self._data_to_write_to_local = []
			self._write_to_sock(data, self._local_sock)
		else:
			self._update_stream(STREAM_DOWN, WAIT_STATUS_READING)

	def _on_local_error(self):
		logging.debug('_on_local_error')
		self._update_activity()
		self.destroy()


	def handle_event(self, sock, event):
		if self._stage == STAGE_DESTROYED:
			logging.debug('ignore handle_event: destroyed')
			return

		if sock == self._remote_sock:
			if event & eventloop.POLL_ERR:
				self._on_remote_error()
				if self._stage == STAGE_DESTROYED:
					return

			if event & (eventloop.POLL_IN | eventloop.POLL_HUP):
				self._on_remote_read()
				if self._stage == STAGE_DESTROYED:
					return
			if event & eventloop.POLL_OUT:
				self._on_remote_write()
		elif sock == self._local_sock:
			if event & eventloop.POLL_ERR:
				self._on_local_error()
				if self._stage == STAGE_DESTROYED:
					return

			if event & (eventloop.POLL_IN | eventloop.POLL_HUP):
				self._on_local_read()
				if self._stage == STAGE_DESTROYED:
					return
			if event & eventloop.POLL_OUT:
				self._on_local_write()
		else:
			logging.warn('unknown socket')

	def destroy(self):
		logging.debug('destroy')
		if self._stage == STAGE_DESTROYED:
			logging.debug('already destriyed')
			return
		self._stage = STAGE_DESTROYED
		if self._remote_address:
			logging.debug('destroy:%s : %d' %self._remote_address)
		else:
			logging.debug('destroy')
		if self._remote_sock:
			logging.debug('destroying remote')
			self._loop.remove(self._remote_sock)
			del self._fd_to_handlers[self._remote_sock.fileno()]
			self._remote_sock.close()
			self._remote_sock = None
		if self._local_sock:
			logging.debug('destroying local')
			self._loop.remove(self._local_sock)
			del self._fd_to_handlers[self._local_sock.fileno()]
			self._local_sock.close()
			self._local_sock = None
		self._dns_resolver.remove_callback(self._handle_dns_resolved)
		self._server.remove_handler(self)
		logging.debug('destroying over')






