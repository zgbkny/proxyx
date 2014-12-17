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
STAGE_HELLO = 1
STAGE_UDP_ASSOC = 2
STAGE_DNS = 3
STAGE_REPLY = 4
STAGE_STREAM = 5
STAGE_DESTROYED = -1

# stream direction
STREAM_UP = 0
STREAM_DOWN = 1

# stream wait status
WAIT_STATUS_INIT = 0
WAIT_STATUS_READING = 1
WAIT_STATUS_WRITING = 2
WAIT_STATUS_READWRITING = WAIT_STATUS_READING | WAIT_STATUS_WRITING

BUF_SIZE = 32 * 1024
class TCPRelayHandler(object):
	def __init__(self, server, fd_to_handlers, loop, local_sock, config, dns_resolver, is_local):
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

	# message from upstream
	def _on_remote_read(self):
		logging.debug('_on_remote_read')

	def _on_remote_write(self):
		logging.debug('_on_remote_write')

	def _on_remote_error(self):
		logging.debug('_on_remote_error')

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
		if self._request.check_header() == False:
			logging.debug('parse')
			self._request.parse(data)
			if self._request.check_header() == True:
				logging.debug('send after parse')
				self._request.get_host_address_and_port()
		else:
			logging.debug('send')
			self._request.get_host_address_and_port()
		

	def _on_local_write(self):
		logging.debug('_on_local_write')
		

	def _on_local_error(self):
		logging.debug('_on_local_error')



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
			self._local_sock.close()
			self._local_sock = None
		self._dns_resolver.remove_callback(self._handle_dns_resolved)
		self._server.remove_handler(self)






