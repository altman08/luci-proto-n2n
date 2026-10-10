'use strict';
'require form';
'require network';
'require uci';
'require tools.widgets as widgets';

network.registerPatternVirtual(/^n2n-.+$/);

return network.registerProtocol('n2n', {
	getI18n() {
		return _('N2N VPN');
	},

	getIfname() {
		return this._ubus('l3_device') || this.get('tunname') || 'n2n-%s'.format(this.sid);
	},

	getPackageName() {
		return 'luci-proto-n2n';
	},

	isFloating() {
		return true;
	},

	isVirtual() {
		return true;
	},

	getDevices() {
		return null;
	},

	containsDevice(ifname) {
		return network.getIfnameOf(ifname) == this.getIfname();
	},

	renderFormOptions(s) {
		let o;

		o = s.taboption('general', form.DynamicList, 'supernode', _('Supernodes'),
			_('One IPv4 address or hostname with port per entry, for example vpn.example.com:1234.'));
		o.rmempty = false;
		// The empty add-item input is not the list. Require entries when saving.
		o.optional = true;
		o.validate = function(section_id, value) {
			if (value == null || value === '')
				return true;
			const values = Array.isArray(value) ? value : [ value ];
			return values.every(function(endpoint) {
				const match = /^([a-zA-Z0-9][a-zA-Z0-9.-]*):([0-9]{1,5})$/.exec(endpoint);
				return match && Number(match[2]) > 0 && Number(match[2]) <= 65535;
			}) ? true : _('Expecting an IPv4 address or hostname followed by :port.');
		};
		o.parse = function(section_id) {
			if (this.isActive(section_id)) {
				const values = this.formvalue(section_id);
				const error = (values == null || values.length == 0)
					? _('At least one supernode is required.') : this.validate(section_id, values);
				if (error !== true)
					return Promise.reject(new TypeError(_('Supernodes') + ': ' + error));
			}
			return form.DynamicList.prototype.parse.call(this, section_id);
		};

		o = s.taboption('general', form.Value, 'community', _('Community'));
		o.rmempty = false;
		o.validate = function(section_id, value) {
			return value && new TextEncoder().encode(value).length <= 15
				? true : _('The community must contain 1 to 15 bytes.');
		};

		o = s.taboption('general', form.ListValue, 'cipher', _('Payload encryption'));
		o.value('3', 'AES');
		o.value('2', 'Twofish');
		o.value('4', 'ChaCha20');
		o.value('5', 'Speck-CTR');
		o.value('1', _('None (unencrypted)'));
		o.default = '3';
		o.rmempty = false;

		o = s.taboption('general', form.Value, 'key', _('Encryption key'),
			_('Use the same key and cipher on all peers. The key must differ from the community.'));
		o.password = true;
		o.rmempty = false;
		for (const cipher of [ '2', '3', '4', '5' ])
			o.depends('cipher', cipher);
		o.validate = function(section_id, value) {
			if (!value)
				return _('An encryption key is required.');
			return value != s.formvalue(section_id, 'community')
				? true : _('The encryption key must differ from the community.');
		};

		o = s.taboption('general', form.ListValue, 'compression', _('Compression'));
		o.value('0', _('None'));
		o.value('1', 'LZO');
		o.value('2', 'Zstandard');
		o.default = '0';
		o.rmempty = false;

		o = s.taboption('general', form.ListValue, 'mode', _('IPv4 address assignment'));
		o.value('static', _('Static address'));
		o.value('dhcp', _('DHCP from the overlay network'));
		o.default = 'static';
		o.rmempty = false;
		o.description = _('DHCP requires a DHCP server reachable through another N2N peer. Interface up means the local TAP is ready, not that a peer is reachable.');

		o = s.taboption('general', form.Value, 'ipaddr', _('IPv4 address'));
		o.datatype = 'ip4addr("nomask")';
		o.rmempty = false;
		o.depends('mode', 'static');
		o.validate = function(section_id, value) {
			const octets = (value || '').split('.');
			return octets.length == 4 && value != '0.0.0.0' && octets.every(function(octet) {
				return /^(0|[1-9][0-9]{0,2})$/.test(octet) && Number(octet) <= 255;
			}) ? true : _('A nonzero IPv4 address is required.');
		};

		o = s.taboption('general', form.Value, 'prefix', _('IPv4 prefix length'));
		o.datatype = 'range(0,32)';
		o.default = '24';
		o.rmempty = false;
		o.depends('mode', 'static');

		o = s.taboption('general', form.Value, 'gateway', _('IPv4 gateway'),
			_('Optional gateway inside the overlay. Leave empty for peer-to-peer or site-to-site use.'));
		o.datatype = 'ip4addr("nomask")';
		o.depends('mode', 'static');

		o = s.taboption('advanced', form.Value, 'tunname', _('TAP device name'));
		o.placeholder = 'n2n-%s'.format(this.sid);
		o.validate = function(section_id, value) {
			const name = value || 'n2n-%s'.format(section_id);
			if (!/^[a-zA-Z0-9_-]{1,15}$/.test(name))
				return _('Use 1 to 15 letters, digits, underscores or hyphens. Set a shorter device name if the interface name is too long.');
			let conflict = false;
			uci.sections('network', 'interface', function(section) {
				if (section.proto == 'n2n' && section['.name'] != section_id &&
					(section.tunname || 'n2n-%s'.format(section['.name'])) == name)
					conflict = true;
			});
			return conflict ? _('This TAP device name is already used by another N2N interface.') : true;
		};

		o = s.taboption('advanced', widgets.NetworkSelect, 'tunlink', _('Underlying interface'),
			_('Optional uplink used to reach the supernodes. Do not select the N2N interface itself.'));
		o.nocreate = true;
		o.exclude = this.sid;
		o.validate = function(section_id, value) {
			return value != section_id ? true : _('The underlying interface cannot be this N2N interface.');
		};

		o = s.taboption('advanced', form.Value, 'mtu', _('MTU'));
		o.datatype = 'range(576,1500)';
		o.placeholder = '1290';

		o = s.taboption('advanced', form.Value, 'macaddr', _('TAP MAC address'),
			_('Optional fixed MAC address. Otherwise N2N generates a random address on each start.'));
		o.datatype = 'macaddr';

		o = s.taboption('advanced', form.Value, 'local_port', _('Local UDP port'));
		o.datatype = 'range(0,65535)';
		o.placeholder = '0';
		o.description = _('Zero selects an available port automatically. Fixed ports must be unique across N2N instances.');

		o = s.taboption('advanced', form.Value, 'management_port', _('Management UDP port'));
		o.datatype = 'range(0,65535)';
		o.placeholder = '0';
		o.description = _('Listens on loopback only. Zero selects an available port automatically; it does not disable the management socket.');
		o.validate = function(section_id, value) {
			if (!value)
				return true;
			if (!/^[0-9]{1,5}$/.test(value) || Number(value) > 65535)
				return _('Expecting a port number between 0 and 65535.');
			if (Number(value) == 0)
				return true;
			let conflict = false;
			uci.sections('network', 'interface', function(section) {
				if (section.proto == 'n2n' && section['.name'] != section_id &&
					Number(section.management_port) == Number(value))
					conflict = true;
			});
			return conflict ? _('This management port is already used by another N2N interface.') : true;
		};

		o = s.taboption('advanced', form.Flag, 'routing', _('Allow routed traffic'),
			_('Allow traffic from other hosts, such as LAN clients. Configure routes and firewall forwarding separately. Always enabled in DHCP mode.'));
		o.default = o.enabled;
		o.rmempty = false;

		o = s.taboption('advanced', form.Flag, 'header_encryption', _('Encrypt packet headers'),
			_('The supernode must be configured for the community and header encryption.'));
		o.rmempty = false;

		o = s.taboption('advanced', form.Flag, 'multicast', _('Forward multicast traffic'));
		o.rmempty = false;

		o = s.taboption('advanced', form.Value, 'startup_timeout', _('TAP startup timeout (seconds)'));
		o.datatype = 'range(5,120)';
		o.placeholder = '30';

		o = s.taboption('advanced', form.Flag, 'peerdns', _('Use DNS servers advertised by peer'));
		o.default = o.enabled;
		o.rmempty = false;
		o.depends('mode', 'dhcp');

		o = s.taboption('advanced', form.Value, 'hostname', _('DHCP hostname'));
		o.datatype = 'hostname';
		o.depends('mode', 'dhcp');
	}
});
