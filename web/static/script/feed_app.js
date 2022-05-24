'use strict';

!function(){
    // websocket to recieve event updates
    let _client,
    // url params
    _params = new URLSearchParams(window.location.search),
    _pub_k = _params.get('pub_k'),
    // gui objs
        // main container where we'll draw out the events
        _text_con = $('#feed-pane'),
    // data
        _notes_arr,
        _profiles_arr,
        _profiles_lookup,
        _profile_draw_required = true,
    // inline media where we can, where false just the link is inserted
        _enable_media = true;

    function draw_notes(){
        let tmpl = [
            '{{#notes}}',
                '<span style="height:60px;width:120px; word-break: break-all; display:table-cell; background-color:#111111" >',
                    '{{#picture}}',
                        '<img src="{{picture}}" width="64" height="64" style="object-fit: cover;border-radius: 50%;" />',
                    '{{/picture}}',
                '</span>',
                '<span style="height:60px;width:100%; display:table-cell;word-break: break-all;vertical-align:top; background-color:#221124" >',
                    '<div>',
                        '<span style="font-weight:bold">{{name}}</span>@shortcode',
                    '</div>',
                    '{{{content}}}',
                '</span><br>',
            '{{/notes}}'
        ].join('');

        let to_show = [];

        if(_profiles_lookup!==undefined){
            _profile_draw_required = false;
        }
        // we don't have the notes yet, profiles must have come back first
        if(_notes_arr===undefined){
            return;
        }

        _notes_arr.forEach(function(c_note){
            let name = c_note['pubkey'],
                p,
                attrs,
                pub_k = c_note['pubkey'],
                to_add = {
                    'content' : c_note['html'],
                    'name' : pub_k.substring(0, 5) + '...' + pub_k.substring(pub_k.length-5)
                };

            if(_profiles_lookup!==undefined){
                p = _profiles_lookup[name];
                if(p!==undefined){
                    attrs = p['attrs'];
                    if(attrs!==undefined){
                        if(attrs['name']!==undefined){
                            to_add['name'] = attrs['name'];
                        }
                        if(attrs['picture']!==undefined){
                            to_add['picture'] = attrs['picture'];
                        }

                    }
                }
            }

            to_show.push(to_add);
        });

        _text_con.html(Mustache.render(tmpl, {
            'notes' : to_show,
        }));
    }

    function start_client(){
        APP.nostr_client.create('ws://localhost:8080/websocket', function(client){
            _client = client;
        },
        function(data){
            _notes_arr.unshift(data);
            draw_notes();
        });
    }

    function load_notes(){
        /*
            puts in breaklines, todo a for links
        */
        function media_lookup(ref_str){
            let media_types = {
                'jpg': 'image',
                'png': 'image',
                'mp4': 'video'
                },
                parts = ref_str.split('.'),
                ext = parts[parts.length-1],
                ret = 'external_link';

            if(ext in media_types && _enable_media){
                ret = media_types[ext];
            }

            return ret;
        }


        function note_html(the_note){
            let http_regex = /(https?\:\/\/[\w\.\/\-\%\?\=]*)/g,
                http_matches,
                link_tmpl = '<a href="{{url}}">{{url}}</a>',
                img_tmpl = '<img src="{{url}}" width=640 height=auto style="display:block;" />',
                video_tmpl = '<video width=640 height=auto style="display:block" controls>' +
                '<source src="{{url}}" >' +
                'Your browser does not support the video tag.' +
                '</video>',
                tmpl_lookup = {
                    'image' : img_tmpl,
                    'external_link' : link_tmpl,
                    'video' : video_tmpl
                };

            the_note['html'] = the_note['content'].replace(/\n/g,'<br>');
            http_matches = the_note['content'].match(http_regex);
            if(http_matches!==null){
                http_matches.forEach(function(c_match){
                    let media_type = media_lookup(c_match);
//                    alert(media_type);
                    the_note['html'] = the_note['html'].replace(c_match,Mustache.render(tmpl_lookup[media_type],{
                        'url' : c_match
                    }));
                });
            }

//            the_note['html'] = the_note['html'].replace(http_regex, '<a href="something"">we replace shit here</a>')
        }

        APP.remote.load_notes_for_profile({
            'pub_k': _pub_k,
            'success': function(data){
                _notes_arr = data['events'];
                _notes_arr.forEach(function(c_note){
                    note_html(c_note);
                });
                draw_notes();
            }
        });
    }

    function load_profiles(){
        APP.remote.load_profiles(function(data){
            _profiles_arr = data['profiles'];
            _profiles_lookup = {};
            _profiles_arr.forEach(function(p){
                _profiles_lookup[p['pub_k']] = p;
            });
            if(_profile_draw_required){
                draw_notes();
            }
        });
    }

    // start when everything is ready
    $(document).ready(function() {
        if (_pub_k===null){
            alert('no pub_k!!!')
        }else{
            // start client for future notes....
            load_notes();
            // obvs this way of doing profile lookups isn't going to scale...
            load_profiles();
        }

    });
}();