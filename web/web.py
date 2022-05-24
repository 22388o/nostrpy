from __future__ import annotations
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    pass

import json
from json import JSONDecodeError
from bottle import request, Bottle, static_file, abort
import logging
from pathlib import Path
from gevent.pywsgi import WSGIServer
from geventwebsocket import WebSocketError
from geventwebsocket.handler import WebSocketHandler
from nostr.event.event import Event
from nostr.ident.profile import ProfileEventHandler, ProfileList, Profile, Contact
from nostr.ident.persist import SQLiteProfileStore
from nostr.event.persist import ClientEventStoreInterface, SQLiteEventStore
from nostr.encrypt import Keys


class StaticServer:
    """
        simple server that just deals with the static data html,js, css...
    """
    def __init__(self, file_root):
        self._app = Bottle()
        self._file_root = file_root+'/'

        """
            basically or statics are the same just ext and name of sub dir and the route text that changes
            so each call this to get the method that'll be called by their route that defines sub_dir and ext for that 
            files type
        """
        def get_for_ftype(sub_dir, ext=None):
            # where not defined so e.g. sub_dir == /css and file ext == .css
            if ext is None:
                ext = sub_dir

            def for_type(name):
                my_root = self._file_root + '%s/' % sub_dir
                logging.debug('StaticServer:: root: %s sub_dir: %s name: %s ext: %s' % (self._file_root,
                                                                                        sub_dir,
                                                                                        name,
                                                                                        ext))

                # if ext not already included then we'll add it
                if not ('.%s' % ext) in name:
                    name = name + '.%s' % ext
                print(name, my_root)
                return static_file(filename=name, root=my_root)
            return for_type

        # html
        html_method = get_for_ftype('html')

        @self._app.route('/html/<name>')
        def html(name):
            return html_method(name)

        # js
        js_method = get_for_ftype('script','js')

        @self._app.route('/script/<name>')
        def js(name):
            return js_method(name)

        # css
        css_method = get_for_ftype('css','css')

        @self._app.route('/css/<name>')
        def css(name):
            return css_method(name)

        @self._app.route('/fonts/<name>')
        def font(name):
            accept = set(['woff2','woff','ttf','svg'])
            splits = name.split('.')
            font_dir = self._file_root + 'fonts/'
            if len(splits)>1 and splits[1] in accept:
                logging.debug('StaticServer::%s %s %s' % (splits[1],
                                                          font_dir,
                                                          name))

                ret = static_file(filename=name, root=font_dir)

            else:
                # TODO this should readlly be doing a ?501? Auth exception
                raise Exception('arrrgh font type not accepted')
            return ret

    def start(self, host='localhost', port=8080):
        logging.debug('started web server at %s port=%s' % (host, port))
        server = WSGIServer((host, port), self._app, handler_class=WebSocketHandler)
        server.serve_forever()

    def stop(self):
        self._app.close()

class NostrWebException(Exception):
    pass


class NostrWeb(StaticServer):

    def __init__(self,
                 file_root,
                 event_store: ClientEventStoreInterface,
                 profile_store: SQLiteProfileStore,
                 profile_handler: ProfileEventHandler):

        self._event_store = event_store
        self._profile_store = profile_store
        self._profile_handler = profile_handler

        self._web_sockets = {}
        super(NostrWeb, self).__init__(file_root)

        self._add_routes()

        # to use when no limit given for queries
        self._default_query_limit = 100
        # queries will be limited to this even if caller asks for more, None unlimited
        self._max_query = None

    def _add_routes(self):
        # methods wrapped so that if they raise NostrException it'll be returned as json {error: text}
        def _get_err_wrapped(method):
            def _wrapped():
                try:
                    return method()
                except NostrWebException as ne:
                    return ne.args[0]
            return _wrapped

        # this is used if method didn't raise nostrexception, do something better in static server
        def my_internal(e):
            return str(e).replace(',', '<br>')

        self._app.error_handler = {
            500: my_internal
        }

        self._app.route('/profile', callback=self._profile)
        self._app.route('/profiles', callback=self._profiles_list)
        self._app.route('/contact_list',callback=self._contact_list)
        self._app.route('/events', method='POST', callback=_get_err_wrapped(self._events_route))
        self._app.route('/text_events', callback=self._text_events_route)
        self._app.route('/text_events_for_profile', callback=self._text_events_for_profile)
        self._app.route('/websocket', callback=self._handle_websocket)


    def _check_pub_key(self, pub_k):
        if not pub_k:
            raise Exception('pub_k is required')
        if not Keys.is_valid_pubkey(pub_k):
            raise Exception('value - %s doesn\'t look like a valid nostr pub key' % pub_k)

    def _profiles_list(self):
        ret = {
            'profiles': self._profile_handler.profiles.as_arr()
        }
        return ret

    def _profile(self):
        the_profile: Profile
        pub_k = request.query.pub_k
        include_contacts: str = request.query.include_contacts
        include_follows: str = request.query.include_follows

        # will throw if we don't think valid pub_k
        self._check_pub_key(pub_k)
        the_profile = self._profile_handler.profiles.get_profile(pub_k)
        if the_profile is None:
            raise Exception('no profile found for pub_k - %s' % pub_k)

        ret = the_profile.as_dict()

        c: Contact
        # add in contacts if asked for
        if include_contacts.lower() == 'true':
            the_profile.load_contacts(self._profile_store)
            ret['contacts'] = [c.contact_public_key for c in the_profile.contacts]

        # add in follows if asked for
        if include_follows.lower() == 'true':
            the_profile.load_followers(self._profile_store)
            ret['follows'] = [c.owner_public_key for c in the_profile.followed_by]

        return json.dumps(ret)

    def _contact_list(self):
        pub_k = request.query.pub_k

        # will throw if we don't think valid pub_k
        self._check_pub_key(pub_k)

        for_profile = self._profile_handler.profiles.get_profile(pub_k,
                                                                 create_type=ProfileList.CREATE_PUBLIC)
        contacts = for_profile.load_contacts(self._profile_store)

        return {
            'pub_k_owner': pub_k,
            'contacts': 'TODO'
        }

    def _get_events(self, filter):
        events = self._event_store.get_filter(filter)
        c_evt: Event
        ret = []
        for c_evt in events:
            ret.append(c_evt.event_data())
        return ret

    def _events_route(self):
        """
        returns events that match given nostr filter [{},...]
        :return:
        """

        try:
            filter = json.loads(request.forms['filter'])
        except KeyError as ke:
            raise NostrWebException({
                'error': 'filter is undefined?!'
            })
        except JSONDecodeError as je:
            raise NostrWebException({
                'error': 'unable to decode filter %s' % request.forms['filter']
            })

        if not hasattr(filter, '__iter__') or isinstance(filter, dict):
            filter = [filter]

        limit = self._get_query_limit()
        if limit is not None:
            if 'limit' not in filter[0] or filter[0]['limit']>limit:
                filter[0]['limit'] = limit


        return {
            'events': self._get_events(filter)
        }

    def _get_query_limit(self):
        limit = self._default_query_limit
        try:
            limit = int(request.query.limit)
        except:
            pass

        if self._max_query and limit and limit > self._max_query:
            limit = self._max_query
        return limit

    def _text_events_route(self):
        """
        all the text notes for a given pub_key
        :return:
        """
        pub_k = request.query.pub_k

        # will throw if we don't think valid pub_k
        self._check_pub_key(pub_k)

        return {
            'events': self._get_events({
                'authors': [pub_k],
                'kinds': [Event.KIND_TEXT_NOTE],
                'limit': self._get_query_limit()
            })
        }

    def _text_events_for_profile(self):
        """
        get texts notes for pub_k or those we have as contacts
        :return:
        """
        pub_k = request.query.pub_k

        # will throw if we don't think valid pub_k
        self._check_pub_key(pub_k)

        limit = self._get_query_limit()

        for_profile = self._profile_handler.profiles.get_profile(pub_k,
                                                                 create_type=ProfileList.CREATE_PUBLIC)
        for_profile.load_contacts(self._profile_store)
        c: Contact

        print([c.contact_public_key for c in for_profile.contacts])

        return {
            'events': self._get_events({
                'authors': [pub_k] + [c.contact_public_key for c in for_profile.contacts],
                'kinds': [Event.KIND_TEXT_NOTE],
                'limit': limit
            })
        }

    def do_event(self, sub_id, evt:Event, relay):
        for c_sock in self._web_sockets:
            ws = self._web_sockets[c_sock]
            try:
                ws.send(json.dumps(evt.event_data()))
            except Exception as e:
                print(e, ws)
                print('kill this guy?')

    def _handle_websocket(self):
        logging.debug('Websocket opened')
        wsock = request.environ.get('wsgi.websocket')
        if not wsock:
            abort(400, 'Expected WebSocket request.')

        self._web_sockets[str(wsock)] = wsock
        while True:
            try:
                # this is just to keep alive, currently we're doing nothing with dead sockets....
                wsock.receive()
            except WebSocketError:
                break

def nostr_web():
    nostr_db_file = '%s/.nostrpy/nostr-client.db' % Path.home()

    from nostr.util import util_funcs
    util_funcs.create_sqlite_store(nostr_db_file)

    event_store = SQLiteEventStore(nostr_db_file)
    profile_store = SQLiteProfileStore(nostr_db_file)
    profile_handler = ProfileEventHandler(SQLiteProfileStore(nostr_db_file))

    my_server = NostrWeb(file_root='%s/PycharmProjects/nostrpy/web/static/' % Path.home(),
                         event_store=event_store,
                         profile_store=profile_store,
                         profile_handler=profile_handler)

    from nostr.client.client import ClientPool
    from datetime import datetime
    def my_connect(the_client):
        the_client.subscribe(handlers=my_server, filters={
            'since': util_funcs.date_as_ticks(datetime.now())
        })

    my_client = ClientPool(['ws://localhost:8081','wss://nostr-pub.wellorder.net'], on_connect=my_connect)
    my_client.start()

    # example clean exit... need to look into more though
    import signal
    import sys
    def sigint_handler(signal, frame):
        my_client.end()
        my_server.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, sigint_handler)

    my_server.start()



if __name__ == '__main__':
    logging.getLogger().setLevel(logging.DEBUG)
    nostr_web()


    # from nostr.ident.persist import SQLiteProfileStore
    #
    # db_file = '%s/.nostrpy/nostr-client.db' % Path.home()
    # my_profile_store = SQLiteProfileStore(db_file)
    # my_event_store = SQLiteEventStore(db_file)
    # my_profile_store.import_contacts_from_events(my_event_store)
