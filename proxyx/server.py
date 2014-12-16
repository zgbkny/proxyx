
from __future__ import absolute_import, division, print_function, with_statement

import sys
import os
import logging
import signal


sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../'))
from proxyx import utils, eventloop, asyncdns, tcprelay

def main():
	config = utils.get_config()

	logging.debug('server main()')

	dns_resolver = asyncdns.DNSResolver()

	tcp_server = tcprelay.TCPRelay(config, dns_resolver, False)


	def run_server():
		def child_handler(signum, _):
			logging.warn('receive SIGQUIT, doing graceful shutting down..')

		signal.signal(getattr(signal, 'SIGQUIT', signal.SIGTERM), child_handler)
		try:
			loop = eventloop.EventLoop()
			dns_resolver.add_to_loop(loop)
			tcp_server.add_to_loop(loop)
			loop.run()

		except (KeyboardInterrupt, IOError, OSError) as e:
			logging.error(e)
			os._exit(1)
	if int(config['workers']) > 1:
		pass
	else:
		run_server()


#if __main__ == '__main__':
#	main()

main()