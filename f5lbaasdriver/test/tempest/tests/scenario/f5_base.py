# Copyright 2016 F5 Networks Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
#   Unless required by applicable law or agreed to in writing, software
#   distributed under the License is distributed on an "AS IS" BASIS,
#   WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#   See the License for the specific language governing permissions and
#   limitations under the License.

from neutron_lbaas.tests.tempest.v2.scenario import base

from f5lbaasdriver.test.tempest.services.clients import l7policy_client
from f5lbaasdriver.test.tempest.services.clients import l7rule_client

from tempest import config
from tempest.scenario import network_resources as net_resources

import six


config = config.CONF


class F5BaseTestCase(base.BaseTestCase):
    '''F5 implementation of base class for scenario testing.'''

    def setUp(self):
        super(F5BaseTestCase, self).setUp()
        self.l7policies = []
        self.l7rules = {}
        self.l7policy_client = l7policy_client.L7PolicyClientJSON(
            *self.client_args)
        self.l7rule_client = l7rule_client.L7RuleClientJSON(
            *self.client_args)
        self._create_servers()
        self._start_servers()
        self._create_load_balancer()
        self._create_detached_pool()
        self._create_members(
            {
                self.servers['primary']: self.server_fixed_ips[
                    self.servers['primary']]
            },
            load_balancer_id=self.load_balancer.get('id'),
            pool_id=self.pool.get('id'),
            subnet_id=self.subnet.get('id'))
        self._create_members(
            {
                self.servers['secondary']: self.server_fixed_ips[
                    self.servers['secondary']]
            },
            load_balancer_id=self.load_balancer.get('id'),
            pool_id=self.detached_pool.get('id'),
            subnet_id=self.subnet.get('id'))
        # self._check_connection(self.vip_ip)
        self._wait_for_load_balancer_status(self.load_balancer.get('id'))

    def tearDown(self):
        self._delete_l7policy(self.l7policy.get('id'))
        self._stop_servers()
        self._traffic_validation_after_stopping_servers()

    def _create_detached_pool(self):
        pool = {
            'loadbalancer_id': self.load_balancer.get('id'),
            'lb_algorithm': 'ROUND_ROBIN',
            'protocol': 'HTTP'
        }
        self.detached_pool = self.pools_client.create_pool(**pool)
        self._wait_for_load_balancer_status(self.load_balancer.get('id'))
        self.assertTrue(self.detached_pool)
        self.addCleanup(self._cleanup_pool, self.detached_pool.get('id'),
                        load_balancer_id=self.load_balancer.get('id'))

    def _create_load_balancer(self, ip_version=4, persistence_type=None):
        self.create_lb_kwargs = {'tenant_id': self.tenant_id,
                                 'vip_subnet_id': self.subnet['id']}
        self.load_balancer = self.load_balancers_client.create_load_balancer(
            **self.create_lb_kwargs)
        load_balancer_id = self.load_balancer['id']
        self.addCleanup(self._cleanup_load_balancer, load_balancer_id)
        self._wait_for_load_balancer_status(load_balancer_id)

        listener = self._create_listener(load_balancer_id=load_balancer_id)
        self._wait_for_load_balancer_status(load_balancer_id)

        self.pool = self._create_pool(listener_id=listener.get('id'),
                                      persistence_type=persistence_type)
        self._wait_for_load_balancer_status(load_balancer_id)

        # self._create_members(load_balancer_id=load_balancer_id,
        #                     pool_id=self.pool['id'],
        #                     subnet_id=self.subnet['id'])

        self.vip_ip = self.load_balancer.get('vip_address')

        # if the ipv4 is used for lb, then fetch the right values from
        # tempest.conf file
        if ip_version == 4:
            if (config.network.public_network_id and not
                    config.network.project_networks_reachable):
                load_balancer = net_resources.AttributeDict(self.load_balancer)
                self._assign_floating_ip_to_lb_vip(load_balancer)
                self.vip_ip = self.floating_ips[
                    load_balancer.id][0]['floating_ip_address']

        # Currently the ovs-agent is not enforcing security groups on the
        # vip port - see https://bugs.launchpad.net/neutron/+bug/1163569
        # However the linuxbridge-agent does, and it is necessary to add a
        # security group with a rule that allows tcp port 80 to the vip port.
        self.ports_client.update_port(
            self.load_balancer.get('vip_port_id'),
            security_groups=[self.security_group.id])

    def _create_members(self, server_list, load_balancer_id=None, pool_id=None,
                        subnet_id=None):
        """Create two members.

        In case there is only one server, create both members with the same ip
        but with different ports to listen on.
        """

        for server_id, ip in six.iteritems(server_list):
            if len(self.server_fixed_ips) == 1:
                member1 = self.members_client.create_member(
                    pool_id=pool_id,
                    address=ip,
                    protocol_port=self.port1,
                    subnet_id=subnet_id)
                self._wait_for_load_balancer_status(load_balancer_id)
                member2 = self.members_client.create_member(
                    pool_id=pool_id,
                    address=ip,
                    protocol_port=self.port2,
                    subnet_id=subnet_id)
                self._wait_for_load_balancer_status(load_balancer_id)
                self.members.extend([member1, member2])
            else:
                member = self.members_client.create_member(
                    pool_id=pool_id,
                    address=ip,
                    protocol_port=self.port1,
                    subnet_id=subnet_id)
                self._wait_for_load_balancer_status(load_balancer_id)
                self.members.append(member)
        self.assertTrue(self.members)

    def _create_l7policy(self, wait=True, **l7policy_kwargs):
        l7policy = self.l7policy_client.create_l7policy(**l7policy_kwargs)
        if wait:
            self._wait_for_load_balancer_status(self.load_balancer.get('id'))
        return l7policy

    def _delete_l7policy(self, l7policy_id, wait=True):
        self.l7policy_client.delete_l7policy(l7policy_id)
        if wait:
            self._wait_for_load_balancer_status(self.load_balancer.get('id'))