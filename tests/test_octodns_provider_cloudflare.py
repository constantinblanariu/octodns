#
#
#

from __future__ import absolute_import, division, print_function, \
    unicode_literals

from mock import Mock, call
from os.path import dirname, join
from requests import HTTPError
from requests_mock import ANY, mock as requests_mock
from unittest import TestCase

from octodns.record import Record, Update
from octodns.provider.base import Plan
from octodns.provider.cloudflare import CloudflareProvider, \
    CloudflareRateLimitError
from octodns.provider.yaml import YamlProvider
from octodns.zone import Zone


def set_record_proxied_flag(record, proxied):
    try:
        record._octodns['cloudflare']['proxied'] = proxied
    except KeyError:
        record._octodns['cloudflare'] = {
            'proxied': proxied
        }

    return record


class TestCloudflareProvider(TestCase):
    expected = Zone('unit.tests.', [])
    source = YamlProvider('test', join(dirname(__file__), 'config'))
    source.populate(expected)

    # Our test suite differs a bit, add our NS and remove the simple one
    expected.add_record(Record.new(expected, 'under', {
        'ttl': 3600,
        'type': 'NS',
        'values': [
            'ns1.unit.tests.',
            'ns2.unit.tests.',
        ]
    }))
    for record in list(expected.records):
        if record.name == 'sub' and record._type == 'NS':
            expected._remove_record(record)
            break

    empty = {'result': [], 'result_info': {'count': 0, 'per_page': 0}}

    def test_populate(self):
        provider = CloudflareProvider('test', 'email', 'token', retry_period=0)

        # Bad requests
        with requests_mock() as mock:
            mock.get(ANY, status_code=400,
                     text='{"success":false,"errors":[{"code":1101,'
                     '"message":"request was invalid"}],'
                     '"messages":[],"result":null}')

            with self.assertRaises(Exception) as ctx:
                zone = Zone('unit.tests.', [])
                provider.populate(zone)

            self.assertEquals('CloudflareError', type(ctx.exception).__name__)
            self.assertEquals('request was invalid', str(ctx.exception))

        # Bad auth
        with requests_mock() as mock:
            mock.get(ANY, status_code=403,
                     text='{"success":false,"errors":[{"code":9103,'
                     '"message":"Unknown X-Auth-Key or X-Auth-Email"}],'
                     '"messages":[],"result":null}')

            with self.assertRaises(Exception) as ctx:
                zone = Zone('unit.tests.', [])
                provider.populate(zone)
            self.assertEquals('CloudflareAuthenticationError',
                              type(ctx.exception).__name__)
            self.assertEquals('Unknown X-Auth-Key or X-Auth-Email',
                              str(ctx.exception))

        # Bad auth, unknown resp
        with requests_mock() as mock:
            mock.get(ANY, status_code=403, text='{}')

            with self.assertRaises(Exception) as ctx:
                zone = Zone('unit.tests.', [])
                provider.populate(zone)
            self.assertEquals('CloudflareAuthenticationError',
                              type(ctx.exception).__name__)
            self.assertEquals('Cloudflare error', str(ctx.exception))

        # General error
        with requests_mock() as mock:
            mock.get(ANY, status_code=502, text='Things caught fire')

            with self.assertRaises(HTTPError) as ctx:
                zone = Zone('unit.tests.', [])
                provider.populate(zone)
            self.assertEquals(502, ctx.exception.response.status_code)

        # Rate Limit error
        with requests_mock() as mock:
            mock.get(ANY, status_code=429,
                     text='{"success":false,"errors":[{"code":10100,'
                     '"message":"More than 1200 requests per 300 seconds '
                     'reached. Please wait and consider throttling your '
                     'request speed"}],"messages":[],"result":null}')

            with self.assertRaises(Exception) as ctx:
                zone = Zone('unit.tests.', [])
                provider.populate(zone)

            self.assertEquals('CloudflareRateLimitError',
                              type(ctx.exception).__name__)
            self.assertEquals('More than 1200 requests per 300 seconds '
                              'reached. Please wait and consider throttling '
                              'your request speed', str(ctx.exception))

        # Rate Limit error, unknown resp
        with requests_mock() as mock:
            mock.get(ANY, status_code=429, text='{}')

            with self.assertRaises(Exception) as ctx:
                zone = Zone('unit.tests.', [])
                provider.populate(zone)

            self.assertEquals('CloudflareRateLimitError',
                              type(ctx.exception).__name__)
            self.assertEquals('Cloudflare error', str(ctx.exception))

        # Non-existent zone doesn't populate anything
        with requests_mock() as mock:
            mock.get(ANY, status_code=200, json=self.empty)

            zone = Zone('unit.tests.', [])
            provider.populate(zone)
            self.assertEquals(set(), zone.records)

        # re-populating the same non-existent zone uses cache and makes no
        # calls
        again = Zone('unit.tests.', [])
        provider.populate(again)
        self.assertEquals(set(), again.records)

        # bust zone cache
        provider._zones = None

        # existing zone with data
        with requests_mock() as mock:
            base = 'https://api.cloudflare.com/client/v4/zones'

            # zones
            with open('tests/fixtures/cloudflare-zones-page-1.json') as fh:
                mock.get(f'{base}?page=1', status_code=200, text=fh.read())
            with open('tests/fixtures/cloudflare-zones-page-2.json') as fh:
                mock.get(f'{base}?page=2', status_code=200, text=fh.read())
            mock.get(f'{base}?page=3', status_code=200,
                     json={'result': [], 'result_info': {'count': 0,
                                                         'per_page': 0}})

            base = f'{base}/234234243423aaabb334342aaa343435'

            # pagerules/URLFWD
            with open('tests/fixtures/cloudflare-pagerules.json') as fh:
                mock.get(f'{base}/pagerules?status=active',
                         status_code=200, text=fh.read())

            # records
            base = f'{base}/dns_records'
            with open('tests/fixtures/cloudflare-dns_records-'
                      'page-1.json') as fh:
                mock.get(f'{base}?page=1', status_code=200, text=fh.read())
            with open('tests/fixtures/cloudflare-dns_records-'
                      'page-2.json') as fh:
                mock.get(f'{base}?page=2', status_code=200, text=fh.read())
            with open('tests/fixtures/cloudflare-dns_records-'
                      'page-3.json') as fh:
                mock.get(f'{base}?page=3', status_code=200, text=fh.read())

            zone = Zone('unit.tests.', [])
            provider.populate(zone)
            self.assertEquals(19, len(zone.records))

            changes = self.expected.changes(zone, provider)

            self.assertEquals(4, len(changes))

        # re-populating the same zone/records comes out of cache, no calls
        again = Zone('unit.tests.', [])
        provider.populate(again)
        self.assertEquals(19, len(again.records))

    def test_apply(self):
        provider = CloudflareProvider('test', 'email', 'token', retry_period=0)

        provider._request = Mock()

        provider._request.side_effect = [
            self.empty,  # no zones
            {
                'result': {
                    'id': 42,
                }
            },  # zone create
        ] + [None] * 27  # individual record creates

        # non-existent zone, create everything
        plan = provider.plan(self.expected)
        self.assertEquals(17, len(plan.changes))
        self.assertEquals(17, provider.apply(plan))
        self.assertFalse(plan.exists)

        provider._request.assert_has_calls([
            # created the domain
            call('POST', '/zones', data={
                'jump_start': False,
                'name': 'unit.tests'
            }),
            # created at least one of the record with expected data
            call('POST', '/zones/42/dns_records', data={
                'content': 'ns1.unit.tests.',
                'type': 'NS',
                'name': 'under.unit.tests',
                'ttl': 3600
            }),
            # make sure semicolons are not escaped when sending data
            call('POST', '/zones/42/dns_records', data={
                'content': 'v=DKIM1;k=rsa;s=email;h=sha256;'
                           'p=A/kinda+of/long/string+with+numb3rs',
                'type': 'TXT',
                'name': 'txt.unit.tests',
                'ttl': 600
            }),
            # create at least one pagerules
            call('POST', '/zones/42/pagerules', data={
                'targets': [
                    {
                        'target': 'url',
                        'constraint': {
                            'operator': 'matches',
                            'value': 'urlfwd.unit.tests/'
                        }
                    }
                ],
                'actions': [
                    {
                        'id': 'forwarding_url',
                        'value': {
                            'url': 'http://www.unit.tests',
                            'status_code': 302
                        }
                    }
                ],
                'status': 'active'
            }),
        ], True)
        # expected number of total calls
        self.assertEquals(29, provider._request.call_count)

        provider._request.reset_mock()

        provider.zone_records = Mock(return_value=[
            {
                "id": "fc12ab34cd5611334422ab3322997653",
                "type": "A",
                "name": "www.unit.tests",
                "content": "1.2.3.4",
                "proxiable": True,
                "proxied": False,
                "ttl": 300,
                "locked": False,
                "zone_id": "ff12ab34cd5611334422ab3322997650",
                "zone_name": "unit.tests",
                "modified_on": "2017-03-11T18:01:43.420689Z",
                "created_on": "2017-03-11T18:01:43.420689Z",
                "meta": {
                    "auto_added": False
                }
            },
            {
                "id": "fc12ab34cd5611334422ab3322997654",
                "type": "A",
                "name": "www.unit.tests",
                "content": "2.2.3.4",
                "proxiable": True,
                "proxied": False,
                "ttl": 300,
                "locked": False,
                "zone_id": "ff12ab34cd5611334422ab3322997650",
                "zone_name": "unit.tests",
                "modified_on": "2017-03-11T18:01:44.030044Z",
                "created_on": "2017-03-11T18:01:44.030044Z",
                "meta": {
                    "auto_added": False
                }
            },
            {
                "id": "fc12ab34cd5611334422ab3322997655",
                "type": "A",
                "name": "nc.unit.tests",
                "content": "3.2.3.4",
                "proxiable": True,
                "proxied": False,
                "ttl": 120,
                "locked": False,
                "zone_id": "ff12ab34cd5611334422ab3322997650",
                "zone_name": "unit.tests",
                "modified_on": "2017-03-11T18:01:44.030044Z",
                "created_on": "2017-03-11T18:01:44.030044Z",
                "meta": {
                    "auto_added": False
                }
            },
            {
                "id": "fc12ab34cd5611334422ab3322997655",
                "type": "A",
                "name": "ttl.unit.tests",
                "content": "4.2.3.4",
                "proxiable": True,
                "proxied": False,
                "ttl": 600,
                "locked": False,
                "zone_id": "ff12ab34cd5611334422ab3322997650",
                "zone_name": "unit.tests",
                "modified_on": "2017-03-11T18:01:44.030044Z",
                "created_on": "2017-03-11T18:01:44.030044Z",
                "meta": {
                    "auto_added": False
                }
            },
            {
                "id": "2a9140b17ffb0e6aed826049eec970b7",
                "targets": [
                    {
                        "target": "url",
                        "constraint": {
                            "operator": "matches",
                            "value": "urlfwd.unit.tests/"
                        }
                    }
                ],
                "actions": [
                    {
                        "id": "forwarding_url",
                        "value": {
                            "url": "https://www.unit.tests",
                            "status_code": 302
                        }
                    }
                ],
                "priority": 1,
                "status": "active",
                "created_on": "2021-06-25T20:10:50.000000Z",
                "modified_on": "2021-06-28T22:38:10.000000Z"
            },
            {
                "id": "2a9141b18ffb0e6aed826050eec970b8",
                "targets": [
                    {
                        "target": "url",
                        "constraint": {
                            "operator": "matches",
                            "value": "urlfwdother.unit.tests/target"
                        }
                    }
                ],
                "actions": [
                    {
                        "id": "forwarding_url",
                        "value": {
                            "url": "https://target.unit.tests",
                            "status_code": 301
                        }
                    }
                ],
                "priority": 2,
                "status": "active",
                "created_on": "2021-06-25T20:10:50.000000Z",
                "modified_on": "2021-06-28T22:38:10.000000Z"
            },
        ])

        # we don't care about the POST/create return values
        provider._request.return_value = {}

        # Test out the create rate-limit handling, then 9 successes
        provider._request.side_effect = [
            CloudflareRateLimitError('{}'),
        ] + ([None] * 5)

        wanted = Zone('unit.tests.', [])
        wanted.add_record(Record.new(wanted, 'nc', {
            'ttl': 60,  # TTL is below their min
            'type': 'A',
            'value': '3.2.3.4'
        }))
        wanted.add_record(Record.new(wanted, 'ttl', {
            'ttl': 300,  # TTL change
            'type': 'A',
            'value': '3.2.3.4'
        }))
        wanted.add_record(Record.new(wanted, 'urlfwd', {
            'ttl': 300,
            'type': 'URLFWD',
            'value': {
                'path': '/*',  # path change
                'target': 'https://www.unit.tests/',  # target change
                'code': 301,  # status_code change
                'masking': '2',
                'query': 0,
            }
        }))

        plan = provider.plan(wanted)
        # only see the delete & ttl update, below min-ttl is filtered out
        self.assertEquals(4, len(plan.changes))
        self.assertEquals(4, provider.apply(plan))
        self.assertTrue(plan.exists)
        # creates a the new value and then deletes all the old
        provider._request.assert_has_calls([
            call('DELETE', '/zones/42/'
                 'pagerules/2a9141b18ffb0e6aed826050eec970b8'),
            call('DELETE', '/zones/ff12ab34cd5611334422ab3322997650/'
                 'dns_records/fc12ab34cd5611334422ab3322997653'),
            call('DELETE', '/zones/ff12ab34cd5611334422ab3322997650/'
                 'dns_records/fc12ab34cd5611334422ab3322997654'),
            call('PUT', '/zones/42/dns_records/'
                 'fc12ab34cd5611334422ab3322997655', data={
                     'content': '3.2.3.4',
                     'type': 'A',
                     'name': 'ttl.unit.tests',
                     'proxied': False,
                     'ttl': 300
                 }),
            call('PUT', '/zones/42/pagerules/'
                 '2a9140b17ffb0e6aed826049eec970b7', data={
                     'targets': [
                         {
                             'target': 'url',
                             'constraint': {
                                 'operator': 'matches',
                                 'value': 'urlfwd.unit.tests/*'
                             }
                         }
                     ],
                     'actions': [
                         {
                             'id': 'forwarding_url',
                             'value': {
                                 'url': 'https://www.unit.tests/',
                                 'status_code': 301
                             }
                         }
                     ],
                     'status': 'active',
                 }),
        ])

    def test_update_add_swap(self):
        provider = CloudflareProvider('test', 'email', 'token', retry_period=0)

        provider.zone_records = Mock(return_value=[
            {
                "id": "fc12ab34cd5611334422ab3322997653",
                "type": "A",
                "name": "a.unit.tests",
                "content": "1.1.1.1",
                "proxiable": True,
                "proxied": False,
                "ttl": 300,
                "locked": False,
                "zone_id": "ff12ab34cd5611334422ab3322997650",
                "zone_name": "unit.tests",
                "modified_on": "2017-03-11T18:01:43.420689Z",
                "created_on": "2017-03-11T18:01:43.420689Z",
                "meta": {
                    "auto_added": False
                }
            },
            {
                "id": "fc12ab34cd5611334422ab3322997654",
                "type": "A",
                "name": "a.unit.tests",
                "content": "2.2.2.2",
                "proxiable": True,
                "proxied": False,
                "ttl": 300,
                "locked": False,
                "zone_id": "ff12ab34cd5611334422ab3322997650",
                "zone_name": "unit.tests",
                "modified_on": "2017-03-11T18:01:43.420689Z",
                "created_on": "2017-03-11T18:01:43.420689Z",
                "meta": {
                    "auto_added": False
                }
            },
        ])

        provider._request = Mock()
        provider._request.side_effect = [
            CloudflareRateLimitError('{}'),
            self.empty,  # no zones
            {
                'result': {
                    'id': 42,
                }
            },  # zone create
            None,
            None,
            None,
            None,
        ]

        # Add something and delete something
        zone = Zone('unit.tests.', [])
        existing = Record.new(zone, 'a', {
            'ttl': 300,
            'type': 'A',
            # This matches the zone data above, one to swap, one to leave
            'values': ['1.1.1.1', '2.2.2.2'],
        })
        new = Record.new(zone, 'a', {
            'ttl': 300,
            'type': 'A',
            # This leaves one, swaps ones, and adds one
            'values': ['2.2.2.2', '3.3.3.3', '4.4.4.4'],
        })
        change = Update(existing, new)
        plan = Plan(zone, zone, [change], True)
        provider._apply(plan)

        # get the list of zones, create a zone, add some records, update
        # something, and delete something
        provider._request.assert_has_calls([
            call('GET', '/zones', params={'page': 1, 'per_page': 50}),
            call('POST', '/zones', data={
                'jump_start': False,
                'name': 'unit.tests'
            }),
            call('POST', '/zones/42/dns_records', data={
                'content': '4.4.4.4',
                'type': 'A',
                'name': 'a.unit.tests',
                'proxied': False,
                'ttl': 300
            }),
            call('PUT', '/zones/42/dns_records/'
                 'fc12ab34cd5611334422ab3322997654', data={
                     'content': '2.2.2.2',
                     'type': 'A',
                     'name': 'a.unit.tests',
                     'proxied': False,
                     'ttl': 300
                 }),
            call('PUT', '/zones/42/dns_records/'
                 'fc12ab34cd5611334422ab3322997653', data={
                     'content': '3.3.3.3',
                     'type': 'A',
                     'name': 'a.unit.tests',
                     'proxied': False,
                     'ttl': 300
                 }),
        ])

    def test_update_delete(self):
        # We need another run so that we can delete, we can't both add and
        # delete in one go b/c of swaps
        provider = CloudflareProvider('test', 'email', 'token', retry_period=0)

        provider.zone_records = Mock(return_value=[
            {
                "id": "fc12ab34cd5611334422ab3322997653",
                "type": "NS",
                "name": "unit.tests",
                "content": "ns1.foo.bar",
                "proxiable": True,
                "proxied": False,
                "ttl": 300,
                "locked": False,
                "zone_id": "ff12ab34cd5611334422ab3322997650",
                "zone_name": "unit.tests",
                "modified_on": "2017-03-11T18:01:43.420689Z",
                "created_on": "2017-03-11T18:01:43.420689Z",
                "meta": {
                    "auto_added": False
                }
            },
            {
                "id": "fc12ab34cd5611334422ab3322997654",
                "type": "NS",
                "name": "unit.tests",
                "content": "ns2.foo.bar",
                "proxiable": True,
                "proxied": False,
                "ttl": 300,
                "locked": False,
                "zone_id": "ff12ab34cd5611334422ab3322997650",
                "zone_name": "unit.tests",
                "modified_on": "2017-03-11T18:01:43.420689Z",
                "created_on": "2017-03-11T18:01:43.420689Z",
                "meta": {
                    "auto_added": False
                }
            },
            {
                "id": "2a9140b17ffb0e6aed826049eec974b7",
                "targets": [
                    {
                        "target": "url",
                        "constraint": {
                            "operator": "matches",
                            "value": "urlfwd1.unit.tests/"
                        }
                    }
                ],
                "actions": [
                    {
                        "id": "forwarding_url",
                        "value": {
                            "url": "https://www.unit.tests",
                            "status_code": 302
                        }
                    }
                ],
                "priority": 1,
                "status": "active",
                "created_on": "2021-06-25T20:10:50.000000Z",
                "modified_on": "2021-06-28T22:38:10.000000Z"
            },
            {
                "id": "2a9141b18ffb0e6aed826054eec970b8",
                "targets": [
                    {
                        "target": "url",
                        "constraint": {
                            "operator": "matches",
                            "value": "urlfwd1.unit.tests/target"
                        }
                    }
                ],
                "actions": [
                    {
                        "id": "forwarding_url",
                        "value": {
                            "url": "https://target.unit.tests",
                            "status_code": 301
                        }
                    }
                ],
                "priority": 2,
                "status": "active",
                "created_on": "2021-06-25T20:10:50.000000Z",
                "modified_on": "2021-06-28T22:38:10.000000Z"
            },
        ])

        provider._request = Mock()
        provider._request.side_effect = [
            CloudflareRateLimitError('{}'),
            self.empty,  # no zones
            {
                'result': {
                    'id': 42,
                }
            },  # zone create
            None,
            None,
            None,
            None,
        ]

        # Add something and delete something
        zone = Zone('unit.tests.', [])
        existing = Record.new(zone, '', {
            'ttl': 300,
            'type': 'NS',
            # This matches the zone data above, one to delete, one to leave
            'values': ['ns1.foo.bar.', 'ns2.foo.bar.'],
        })
        exstingurlfwd = Record.new(zone, 'urlfwd1', {
            'ttl': 300,
            'type': 'URLFWD',
            'values': [
                {
                    'path': '/',
                    'target': 'https://www.unit.tests',
                    'code': 302,
                    'masking': '2',
                    'query': 0,
                },
                {
                    'path': '/target',
                    'target': 'https://target.unit.tests',
                    'code': 301,
                    'masking': '2',
                    'query': 0,
                }
            ]
        })
        new = Record.new(zone, '', {
            'ttl': 300,
            'type': 'NS',
            # This leaves one and deletes one
            'value': 'ns2.foo.bar.',
        })
        newurlfwd = Record.new(zone, 'urlfwd1', {
            'ttl': 300,
            'type': 'URLFWD',
            'value': {
                'path': '/',
                'target': 'https://www.unit.tests',
                'code': 302,
                'masking': '2',
                'query': 0,
            }
        })
        change = Update(existing, new)
        changeurlfwd = Update(exstingurlfwd, newurlfwd)
        plan = Plan(zone, zone, [change, changeurlfwd], True)
        provider._apply(plan)

        # Get zones, create zone, create a record, delete a record
        provider._request.assert_has_calls([
            call('GET', '/zones', params={'page': 1, 'per_page': 50}),
            call('POST', '/zones', data={
                'jump_start': False,
                'name': 'unit.tests'
            }),
            call('PUT', '/zones/42/dns_records/'
                 'fc12ab34cd5611334422ab3322997654', data={
                     'content': 'ns2.foo.bar.',
                     'type': 'NS',
                     'name': 'unit.tests',
                     'ttl': 300
                 }),
            call('DELETE', '/zones/42/dns_records/'
                 'fc12ab34cd5611334422ab3322997653'),
            call('PUT', '/zones/42/pagerules/'
                 '2a9140b17ffb0e6aed826049eec974b7', data={
                     'targets': [
                         {
                             'target': 'url',
                             'constraint': {
                                 'operator': 'matches',
                                 'value': 'urlfwd1.unit.tests/'
                             }
                         }
                     ],
                     'actions': [
                         {
                             'id': 'forwarding_url',
                             'value': {
                                 'url': 'https://www.unit.tests',
                                 'status_code': 302
                             }
                         }
                     ],
                     'status': 'active'
                 }),
            call('DELETE', '/zones/42/pagerules/'
                 '2a9141b18ffb0e6aed826054eec970b8'),
        ])

    def test_ptr(self):
        provider = CloudflareProvider('test', 'email', 'token')

        zone = Zone('unit.tests.', [])
        # PTR record
        ptr_record = Record.new(zone, 'ptr', {
            'ttl': 300,
            'type': 'PTR',
            'value': 'foo.bar.com.'
        })

        ptr_record_contents = provider._gen_data(ptr_record)
        self.assertEquals({
            'name': 'ptr.unit.tests',
            'ttl': 300,
            'type': 'PTR',
            'content': 'foo.bar.com.'
        }, list(ptr_record_contents)[0])

    def test_loc(self):
        self.maxDiff = None
        provider = CloudflareProvider('test', 'email', 'token')

        zone = Zone('unit.tests.', [])
        # LOC record
        loc_record = Record.new(zone, 'example', {
            'ttl': 300,
            'type': 'LOC',
            'value': {
                'lat_degrees': 31,
                'lat_minutes': 58,
                'lat_seconds': 52.1,
                'lat_direction': 'S',
                'long_degrees': 115,
                'long_minutes': 49,
                'long_seconds': 11.7,
                'long_direction': 'E',
                'altitude': 20,
                'size': 10,
                'precision_horz': 10,
                'precision_vert': 2,
            }
        })

        loc_record_contents = provider._gen_data(loc_record)
        self.assertEquals({
            'name': 'example.unit.tests',
            'ttl': 300,
            'type': 'LOC',
            'data': {
                'lat_degrees': 31,
                'lat_minutes': 58,
                'lat_seconds': 52.1,
                'lat_direction': 'S',
                'long_degrees': 115,
                'long_minutes': 49,
                'long_seconds': 11.7,
                'long_direction': 'E',
                'altitude': 20,
                'size': 10,
                'precision_horz': 10,
                'precision_vert': 2,
            }
        }, list(loc_record_contents)[0])

    def test_srv(self):
        provider = CloudflareProvider('test', 'email', 'token')

        zone = Zone('unit.tests.', [])
        # SRV record not under a sub-domain
        srv_record = Record.new(zone, '_example._tcp', {
            'ttl': 300,
            'type': 'SRV',
            'value': {
                'port': 1234,
                'priority': 0,
                'target': 'nc.unit.tests.',
                'weight': 5
            }
        })
        # SRV record under a sub-domain
        srv_record_with_sub = Record.new(zone, '_example._tcp.sub', {
            'ttl': 300,
            'type': 'SRV',
            'value': {
                'port': 1234,
                'priority': 0,
                'target': 'nc.unit.tests.',
                'weight': 5
            }
        })

        srv_record_contents = provider._gen_data(srv_record)
        srv_record_with_sub_contents = provider._gen_data(srv_record_with_sub)
        self.assertEquals({
            'name': '_example._tcp.unit.tests',
            'ttl': 300,
            'type': 'SRV',
            'data': {
                'service': '_example',
                'proto': '_tcp',
                'name': 'unit.tests.',
                'priority': 0,
                'weight': 5,
                'port': 1234,
                'target': 'nc.unit.tests'
            }
        }, list(srv_record_contents)[0])
        self.assertEquals({
            'name': '_example._tcp.sub.unit.tests',
            'ttl': 300,
            'type': 'SRV',
            'data': {
                'service': '_example',
                'proto': '_tcp',
                'name': 'sub',
                'priority': 0,
                'weight': 5,
                'port': 1234,
                'target': 'nc.unit.tests'
            }
        }, list(srv_record_with_sub_contents)[0])

    def test_alias(self):
        provider = CloudflareProvider('test', 'email', 'token')

        # A CNAME for us to transform to ALIAS
        provider.zone_records = Mock(return_value=[
            {
                "id": "fc12ab34cd5611334422ab3322997642",
                "type": "CNAME",
                "name": "unit.tests",
                "content": "www.unit.tests",
                "proxiable": True,
                "proxied": False,
                "ttl": 300,
                "locked": False,
                "zone_id": "ff12ab34cd5611334422ab3322997650",
                "zone_name": "unit.tests",
                "modified_on": "2017-03-11T18:01:43.420689Z",
                "created_on": "2017-03-11T18:01:43.420689Z",
                "meta": {
                    "auto_added": False
                }
            },
        ])

        zone = Zone('unit.tests.', [])
        provider.populate(zone)
        self.assertEquals(1, len(zone.records))
        record = list(zone.records)[0]
        self.assertEquals('', record.name)
        self.assertEquals('unit.tests.', record.fqdn)
        self.assertEquals('ALIAS', record._type)
        self.assertEquals('www.unit.tests.', record.value)

        # Make sure we transform back to CNAME going the other way
        contents = provider._gen_data(record)
        self.assertEquals({
            'content': 'www.unit.tests.',
            'name': 'unit.tests',
            'proxied': False,
            'ttl': 300,
            'type': 'CNAME'
        }, list(contents)[0])

    def test_gen_key(self):
        provider = CloudflareProvider('test', 'email', 'token')

        for expected, data in (
            ('foo.bar.com.', {
                'content': 'foo.bar.com.',
                'type': 'CNAME',
            }),
            ('10 foo.bar.com.', {
                'content': 'foo.bar.com.',
                'priority': 10,
                'type': 'MX',
            }),
            ('0 tag some-value', {
                'data': {
                    'flags': 0,
                    'tag': 'tag',
                    'value': 'some-value',
                },
                'type': 'CAA',
            }),
            ('42 100 thing-were-pointed.at 101', {
                'data': {
                    'port': 42,
                    'priority': 100,
                    'target': 'thing-were-pointed.at',
                    'weight': 101,
                },
                'type': 'SRV',
            }),
            ('31 58 52.1 S 115 49 11.7 E 20 10 10 2', {
                'data': {
                    'lat_degrees': 31,
                    'lat_minutes': 58,
                    'lat_seconds': 52.1,
                    'lat_direction': 'S',
                    'long_degrees': 115,
                    'long_minutes': 49,
                    'long_seconds': 11.7,
                    'long_direction': 'E',
                    'altitude': 20,
                    'size': 10,
                    'precision_horz': 10,
                    'precision_vert': 2,
                },
                'type': 'LOC',
            }),
        ):
            self.assertEqual(expected, provider._gen_key(data))

    def test_cdn(self):
        provider = CloudflareProvider('test', 'email', 'token', True)

        # A CNAME for us to transform to ALIAS
        provider.zone_records = Mock(return_value=[
            {
                "id": "fc12ab34cd5611334422ab3322997642",
                "type": "CNAME",
                "name": "cname.unit.tests",
                "content": "www.unit.tests",
                "proxiable": True,
                "proxied": True,
                "ttl": 300,
                "locked": False,
                "zone_id": "ff12ab34cd5611334422ab3322997650",
                "zone_name": "unit.tests",
                "modified_on": "2017-03-11T18:01:43.420689Z",
                "created_on": "2017-03-11T18:01:43.420689Z",
                "meta": {
                    "auto_added": False
                }
            },
            {
                "id": "fc12ab34cd5611334422ab3322997642",
                "type": "A",
                "name": "a.unit.tests",
                "content": "1.1.1.1",
                "proxiable": True,
                "proxied": True,
                "ttl": 300,
                "locked": False,
                "zone_id": "ff12ab34cd5611334422ab3322997650",
                "zone_name": "unit.tests",
                "modified_on": "2017-03-11T18:01:43.420689Z",
                "created_on": "2017-03-11T18:01:43.420689Z",
                "meta": {
                    "auto_added": False
                }
            },
            {
                "id": "fc12ab34cd5611334422ab3322997642",
                "type": "A",
                "name": "a.unit.tests",
                "content": "1.1.1.2",
                "proxiable": True,
                "proxied": True,
                "ttl": 300,
                "locked": False,
                "zone_id": "ff12ab34cd5611334422ab3322997650",
                "zone_name": "unit.tests",
                "modified_on": "2017-03-11T18:01:43.420689Z",
                "created_on": "2017-03-11T18:01:43.420689Z",
                "meta": {
                    "auto_added": False
                }
            },
            {
                "id": "fc12ab34cd5611334422ab3322997642",
                "type": "A",
                "name": "multi.unit.tests",
                "content": "1.1.1.3",
                "proxiable": True,
                "proxied": True,
                "ttl": 300,
                "locked": False,
                "zone_id": "ff12ab34cd5611334422ab3322997650",
                "zone_name": "unit.tests",
                "modified_on": "2017-03-11T18:01:43.420689Z",
                "created_on": "2017-03-11T18:01:43.420689Z",
                "meta": {
                    "auto_added": False
                }
            },
            {
                "id": "fc12ab34cd5611334422ab3322997642",
                "type": "AAAA",
                "name": "multi.unit.tests",
                "content": "::1",
                "proxiable": True,
                "proxied": True,
                "ttl": 300,
                "locked": False,
                "zone_id": "ff12ab34cd5611334422ab3322997650",
                "zone_name": "unit.tests",
                "modified_on": "2017-03-11T18:01:43.420689Z",
                "created_on": "2017-03-11T18:01:43.420689Z",
                "meta": {
                    "auto_added": False
                }
            },
        ])

        zone = Zone('unit.tests.', [])
        provider.populate(zone)

        # the two A records get merged into one CNAME record pointing to
        # the CDN.
        self.assertEquals(3, len(zone.records))

        ordered = sorted(zone.records, key=lambda r: r.name)

        record = ordered[0]
        self.assertEquals('a', record.name)
        self.assertEquals('a.unit.tests.', record.fqdn)
        self.assertEquals('CNAME', record._type)
        self.assertEquals('a.unit.tests.cdn.cloudflare.net.', record.value)

        record = ordered[1]
        self.assertEquals('cname', record.name)
        self.assertEquals('cname.unit.tests.', record.fqdn)
        self.assertEquals('CNAME', record._type)
        self.assertEquals('cname.unit.tests.cdn.cloudflare.net.', record.value)

        record = ordered[2]
        self.assertEquals('multi', record.name)
        self.assertEquals('multi.unit.tests.', record.fqdn)
        self.assertEquals('CNAME', record._type)
        self.assertEquals('multi.unit.tests.cdn.cloudflare.net.', record.value)

        # CDN enabled records can't be updated, we don't know the real values
        # never point a Cloudflare record to itself.
        wanted = Zone('unit.tests.', [])
        wanted.add_record(Record.new(wanted, 'cname', {
            'ttl': 300,
            'type': 'CNAME',
            'value': 'change.unit.tests.cdn.cloudflare.net.'
        }))
        wanted.add_record(Record.new(wanted, 'new', {
            'ttl': 300,
            'type': 'CNAME',
            'value': 'new.unit.tests.cdn.cloudflare.net.'
        }))
        wanted.add_record(Record.new(wanted, 'created', {
            'ttl': 300,
            'type': 'CNAME',
            'value': 'www.unit.tests.'
        }))

        plan = provider.plan(wanted)
        self.assertEquals(1, len(plan.changes))

    def test_cdn_alias(self):
        provider = CloudflareProvider('test', 'email', 'token', True)

        # A CNAME for us to transform to ALIAS
        provider.zone_records = Mock(return_value=[
            {
                "id": "fc12ab34cd5611334422ab3322997642",
                "type": "CNAME",
                "name": "unit.tests",
                "content": "www.unit.tests",
                "proxiable": True,
                "proxied": True,
                "ttl": 300,
                "locked": False,
                "zone_id": "ff12ab34cd5611334422ab3322997650",
                "zone_name": "unit.tests",
                "modified_on": "2017-03-11T18:01:43.420689Z",
                "created_on": "2017-03-11T18:01:43.420689Z",
                "meta": {
                    "auto_added": False
                }
            },
        ])

        zone = Zone('unit.tests.', [])
        provider.populate(zone)
        self.assertEquals(1, len(zone.records))
        record = list(zone.records)[0]
        self.assertEquals('', record.name)
        self.assertEquals('unit.tests.', record.fqdn)
        self.assertEquals('ALIAS', record._type)
        self.assertEquals('unit.tests.cdn.cloudflare.net.', record.value)

        # CDN enabled records can't be updated, we don't know the real values
        # never point a Cloudflare record to itself.
        wanted = Zone('unit.tests.', [])
        wanted.add_record(Record.new(wanted, '', {
            'ttl': 300,
            'type': 'ALIAS',
            'value': 'change.unit.tests.cdn.cloudflare.net.'
        }))

        plan = provider.plan(wanted)
        self.assertEquals(False, hasattr(plan, 'changes'))

    def test_unproxiabletype_recordfor_returnsrecordwithnocloudflare(self):
        provider = CloudflareProvider('test', 'email', 'token')
        name = "unit.tests"
        _type = "NS"
        zone_records = [
            {
                "id": "fc12ab34cd5611334422ab3322997654",
                "type": _type,
                "name": name,
                "content": "ns2.foo.bar",
                "proxiable": True,
                "proxied": False,
                "ttl": 300,
                "locked": False,
                "zone_id": "ff12ab34cd5611334422ab3322997650",
                "zone_name": "unit.tests",
                "modified_on": "2017-03-11T18:01:43.420689Z",
                "created_on": "2017-03-11T18:01:43.420689Z",
                "meta": {
                    "auto_added": False
                }
            }
        ]
        provider.zone_records = Mock(return_value=zone_records)
        zone = Zone('unit.tests.', [])
        provider.populate(zone)

        record = provider._record_for(zone, name, _type, zone_records, False)

        self.assertFalse('cloudflare' in record._octodns)

    def test_proxiabletype_recordfor_retrecordwithcloudflareunproxied(self):
        provider = CloudflareProvider('test', 'email', 'token')
        name = "multi.unit.tests"
        _type = "AAAA"
        zone_records = [
            {
                "id": "fc12ab34cd5611334422ab3322997642",
                "type": _type,
                "name": name,
                "content": "::1",
                "proxiable": True,
                "proxied": False,
                "ttl": 300,
                "locked": False,
                "zone_id": "ff12ab34cd5611334422ab3322997650",
                "zone_name": "unit.tests",
                "modified_on": "2017-03-11T18:01:43.420689Z",
                "created_on": "2017-03-11T18:01:43.420689Z",
                "meta": {
                    "auto_added": False
                }
            }
        ]
        provider.zone_records = Mock(return_value=zone_records)
        zone = Zone('unit.tests.', [])
        provider.populate(zone)

        record = provider._record_for(zone, name, _type, zone_records, False)

        self.assertFalse(record._octodns['cloudflare']['proxied'])

    def test_proxiabletype_recordfor_returnsrecordwithcloudflareproxied(self):
        provider = CloudflareProvider('test', 'email', 'token')
        name = "multi.unit.tests"
        _type = "AAAA"
        zone_records = [
            {
                "id": "fc12ab34cd5611334422ab3322997642",
                "type": _type,
                "name": name,
                "content": "::1",
                "proxiable": True,
                "proxied": True,
                "ttl": 300,
                "locked": False,
                "zone_id": "ff12ab34cd5611334422ab3322997650",
                "zone_name": "unit.tests",
                "modified_on": "2017-03-11T18:01:43.420689Z",
                "created_on": "2017-03-11T18:01:43.420689Z",
                "meta": {
                    "auto_added": False
                }
            }
        ]
        provider.zone_records = Mock(return_value=zone_records)
        zone = Zone('unit.tests.', [])
        provider.populate(zone)

        record = provider._record_for(zone, name, _type, zone_records, False)

        self.assertTrue(record._octodns['cloudflare']['proxied'])

    def test_proxiedrecordandnewttl_includechange_returnsfalse(self):
        provider = CloudflareProvider('test', 'email', 'token')
        zone = Zone('unit.tests.', [])
        existing = set_record_proxied_flag(
            Record.new(zone, 'a', {
                'ttl': 1,
                'type': 'A',
                'values': ['1.1.1.1', '2.2.2.2']
            }), True
        )
        new = Record.new(zone, 'a', {
            'ttl': 300,
            'type': 'A',
            'values': ['1.1.1.1', '2.2.2.2']
        })
        change = Update(existing, new)

        include_change = provider._include_change(change)

        self.assertFalse(include_change)

    def test_unproxiabletype_gendata_returnsnoproxied(self):
        provider = CloudflareProvider('test', 'email', 'token')
        zone = Zone('unit.tests.', [])
        record = Record.new(zone, 'a', {
            'ttl': 3600,
            'type': 'NS',
            'value': 'ns1.unit.tests.'
        })

        data = next(provider._gen_data(record))

        self.assertFalse('proxied' in data)

    def test_proxiabletype_gendata_returnsunproxied(self):
        provider = CloudflareProvider('test', 'email', 'token')
        zone = Zone('unit.tests.', [])
        record = set_record_proxied_flag(
            Record.new(zone, 'a', {
                'ttl': 300,
                'type': 'A',
                'value': '1.2.3.4'
            }), False
        )

        data = next(provider._gen_data(record))

        self.assertFalse(data['proxied'])

    def test_proxiabletype_gendata_returnsproxied(self):
        provider = CloudflareProvider('test', 'email', 'token')
        zone = Zone('unit.tests.', [])
        record = set_record_proxied_flag(
            Record.new(zone, 'a', {
                'ttl': 300,
                'type': 'A',
                'value': '1.2.3.4'
            }), True
        )

        data = next(provider._gen_data(record))

        self.assertTrue(data['proxied'])

    def test_createrecord_extrachanges_returnsemptylist(self):
        provider = CloudflareProvider('test', 'email', 'token')
        provider.zone_records = Mock(return_value=[])
        existing = Zone('unit.tests.', [])
        provider.populate(existing)
        provider.zone_records = Mock(return_value=[
            {
                "id": "fc12ab34cd5611334422ab3322997642",
                "type": "CNAME",
                "name": "a.unit.tests",
                "content": "www.unit.tests",
                "proxiable": True,
                "proxied": True,
                "ttl": 300,
                "locked": False,
                "zone_id": "ff12ab34cd5611334422ab3322997650",
                "zone_name": "unit.tests",
                "modified_on": "2017-03-11T18:01:43.420689Z",
                "created_on": "2017-03-11T18:01:43.420689Z",
                "meta": {
                    "auto_added": False
                }
            }
        ])
        desired = Zone('unit.tests.', [])
        provider.populate(desired)
        changes = existing.changes(desired, provider)

        extra_changes = provider._extra_changes(existing, desired, changes)

        self.assertFalse(extra_changes)

    def test_updaterecord_extrachanges_returnsemptylist(self):
        provider = CloudflareProvider('test', 'email', 'token')
        provider.zone_records = Mock(return_value=[
            {
                "id": "fc12ab34cd5611334422ab3322997642",
                "type": "CNAME",
                "name": "a.unit.tests",
                "content": "www.unit.tests",
                "proxiable": True,
                "proxied": True,
                "ttl": 120,
                "locked": False,
                "zone_id": "ff12ab34cd5611334422ab3322997650",
                "zone_name": "unit.tests",
                "modified_on": "2017-03-11T18:01:43.420689Z",
                "created_on": "2017-03-11T18:01:43.420689Z",
                "meta": {
                    "auto_added": False
                }
            }
        ])
        existing = Zone('unit.tests.', [])
        provider.populate(existing)
        provider.zone_records = Mock(return_value=[
            {
                "id": "fc12ab34cd5611334422ab3322997642",
                "type": "CNAME",
                "name": "a.unit.tests",
                "content": "www.unit.tests",
                "proxiable": True,
                "proxied": True,
                "ttl": 300,
                "locked": False,
                "zone_id": "ff12ab34cd5611334422ab3322997650",
                "zone_name": "unit.tests",
                "modified_on": "2017-03-11T18:01:43.420689Z",
                "created_on": "2017-03-11T18:01:43.420689Z",
                "meta": {
                    "auto_added": False
                }
            }
        ])
        desired = Zone('unit.tests.', [])
        provider.populate(desired)
        changes = existing.changes(desired, provider)

        extra_changes = provider._extra_changes(existing, desired, changes)

        self.assertFalse(extra_changes)

    def test_deleterecord_extrachanges_returnsemptylist(self):
        provider = CloudflareProvider('test', 'email', 'token')
        provider.zone_records = Mock(return_value=[
            {
                "id": "fc12ab34cd5611334422ab3322997642",
                "type": "CNAME",
                "name": "a.unit.tests",
                "content": "www.unit.tests",
                "proxiable": True,
                "proxied": True,
                "ttl": 300,
                "locked": False,
                "zone_id": "ff12ab34cd5611334422ab3322997650",
                "zone_name": "unit.tests",
                "modified_on": "2017-03-11T18:01:43.420689Z",
                "created_on": "2017-03-11T18:01:43.420689Z",
                "meta": {
                    "auto_added": False
                }
            }
        ])
        existing = Zone('unit.tests.', [])
        provider.populate(existing)
        provider.zone_records = Mock(return_value=[])
        desired = Zone('unit.tests.', [])
        provider.populate(desired)
        changes = existing.changes(desired, provider)

        extra_changes = provider._extra_changes(existing, desired, changes)

        self.assertFalse(extra_changes)

    def test_proxify_extrachanges_returnsupdatelist(self):
        provider = CloudflareProvider('test', 'email', 'token')
        provider.zone_records = Mock(return_value=[
            {
                "id": "fc12ab34cd5611334422ab3322997642",
                "type": "CNAME",
                "name": "a.unit.tests",
                "content": "www.unit.tests",
                "proxiable": True,
                "proxied": False,
                "ttl": 300,
                "locked": False,
                "zone_id": "ff12ab34cd5611334422ab3322997650",
                "zone_name": "unit.tests",
                "modified_on": "2017-03-11T18:01:43.420689Z",
                "created_on": "2017-03-11T18:01:43.420689Z",
                "meta": {
                    "auto_added": False
                }
            }
        ])
        existing = Zone('unit.tests.', [])
        provider.populate(existing)
        provider.zone_records = Mock(return_value=[
            {
                "id": "fc12ab34cd5611334422ab3322997642",
                "type": "CNAME",
                "name": "a.unit.tests",
                "content": "www.unit.tests",
                "proxiable": True,
                "proxied": True,
                "ttl": 300,
                "locked": False,
                "zone_id": "ff12ab34cd5611334422ab3322997650",
                "zone_name": "unit.tests",
                "modified_on": "2017-03-11T18:01:43.420689Z",
                "created_on": "2017-03-11T18:01:43.420689Z",
                "meta": {
                    "auto_added": False
                }
            }
        ])
        desired = Zone('unit.tests.', [])
        provider.populate(desired)
        changes = existing.changes(desired, provider)

        extra_changes = provider._extra_changes(existing, desired, changes)

        self.assertEquals(1, len(extra_changes))
        self.assertFalse(
            extra_changes[0].existing._octodns['cloudflare']['proxied']
        )
        self.assertTrue(
            extra_changes[0].new._octodns['cloudflare']['proxied']
        )

    def test_unproxify_extrachanges_returnsupdatelist(self):
        provider = CloudflareProvider('test', 'email', 'token')
        provider.zone_records = Mock(return_value=[
            {
                "id": "fc12ab34cd5611334422ab3322997642",
                "type": "CNAME",
                "name": "a.unit.tests",
                "content": "www.unit.tests",
                "proxiable": True,
                "proxied": True,
                "ttl": 300,
                "locked": False,
                "zone_id": "ff12ab34cd5611334422ab3322997650",
                "zone_name": "unit.tests",
                "modified_on": "2017-03-11T18:01:43.420689Z",
                "created_on": "2017-03-11T18:01:43.420689Z",
                "meta": {
                    "auto_added": False
                }
            }
        ])
        existing = Zone('unit.tests.', [])
        provider.populate(existing)
        provider.zone_records = Mock(return_value=[
            {
                "id": "fc12ab34cd5611334422ab3322997642",
                "type": "CNAME",
                "name": "a.unit.tests",
                "content": "www.unit.tests",
                "proxiable": True,
                "proxied": False,
                "ttl": 300,
                "locked": False,
                "zone_id": "ff12ab34cd5611334422ab3322997650",
                "zone_name": "unit.tests",
                "modified_on": "2017-03-11T18:01:43.420689Z",
                "created_on": "2017-03-11T18:01:43.420689Z",
                "meta": {
                    "auto_added": False
                }
            }
        ])
        desired = Zone('unit.tests.', [])
        provider.populate(desired)
        changes = existing.changes(desired, provider)

        extra_changes = provider._extra_changes(existing, desired, changes)

        self.assertEquals(1, len(extra_changes))
        self.assertTrue(
            extra_changes[0].existing._octodns['cloudflare']['proxied']
        )
        self.assertFalse(
            extra_changes[0].new._octodns['cloudflare']['proxied']
        )

    def test_emailless_auth(self):
        provider = CloudflareProvider('test', token='token 123',
                                      email='email 234')
        headers = provider._sess.headers
        self.assertEquals('email 234', headers['X-Auth-Email'])
        self.assertEquals('token 123', headers['X-Auth-Key'])

        provider = CloudflareProvider('test', token='token 123')
        headers = provider._sess.headers
        self.assertEquals('Bearer token 123', headers['Authorization'])

    def test_retry_behavior(self):
        provider = CloudflareProvider('test', token='token 123',
                                      email='email 234', retry_period=0)
        result = {
            "success": True,
            "errors": [],
            "messages": [],
            "result": [],
            "result_info": {
                "count": 1,
                "per_page": 50
            }
        }
        zone = Zone('unit.tests.', [])
        provider._request = Mock()

        # No retry required, just calls and is returned
        provider._zones = None
        provider._request.reset_mock()
        provider._request.side_effect = [result]
        self.assertEquals([], provider.zone_records(zone))
        provider._request.assert_has_calls([call('GET', '/zones',
                                           params={'page': 1,
                                                   'per_page': 50})])

        # One retry required
        provider._zones = None
        provider._request.reset_mock()
        provider._request.side_effect = [
            CloudflareRateLimitError('{}'),
            result
        ]
        self.assertEquals([], provider.zone_records(zone))
        provider._request.assert_has_calls([call('GET', '/zones',
                                           params={'page': 1,
                                                   'per_page': 50})])

        # Two retries required
        provider._zones = None
        provider._request.reset_mock()
        provider._request.side_effect = [
            CloudflareRateLimitError('{}'),
            CloudflareRateLimitError('{}'),
            result
        ]
        self.assertEquals([], provider.zone_records(zone))
        provider._request.assert_has_calls([call('GET', '/zones',
                                           params={'page': 1,
                                                   'per_page': 50})])

        # # Exhaust our retries
        provider._zones = None
        provider._request.reset_mock()
        provider._request.side_effect = [
            CloudflareRateLimitError({"errors": [{"message": "first"}]}),
            CloudflareRateLimitError({"errors": [{"message": "boo"}]}),
            CloudflareRateLimitError({"errors": [{"message": "boo"}]}),
            CloudflareRateLimitError({"errors": [{"message": "boo"}]}),
            CloudflareRateLimitError({"errors": [{"message": "last"}]}),
        ]
        with self.assertRaises(CloudflareRateLimitError) as ctx:
            provider.zone_records(zone)
            self.assertEquals('last', str(ctx.exception))

    def test_ttl_mapping(self):
        provider = CloudflareProvider('test', 'email', 'token')

        self.assertEquals(120, provider._ttl_data(120))
        self.assertEquals(120, provider._ttl_data(120))
        self.assertEquals(3600, provider._ttl_data(3600))
        self.assertEquals(300, provider._ttl_data(1))
