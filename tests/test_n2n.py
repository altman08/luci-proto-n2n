#!/usr/bin/env python3
"""Isolated N2N regression tests; run with python3 tests/test_n2n.py -v.

Run from a standalone feed checkout; no OpenWrt build tree is required. Shell
mocks never run edge, resolve real hosts, sleep, or change the host's network.
Release collection tests use automatically cleaned temporary directories. Bash
is used for setup because dash cannot override the '[' file-check builtin.
Node is optional; frontend tests are skipped explicitly when it is unavailable.
"""

import ast
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import textwrap
import unittest


ROOT = Path(__file__).resolve().parents[1]
SHELL_SOURCE = ROOT / "luci-proto-n2n/root/lib/netifd/proto/n2n.sh"
FRONTEND_SOURCE = ROOT / "luci-proto-n2n/htdocs/luci-static/resources/protocol/n2n.js"
BASH = shutil.which("bash")
NODE = shutil.which("node")


def run_process(argv, input_text=None, extra_env=None):
    env = os.environ.copy()
    # Do not allow startup files or inherited shell functions to defeat mocks.
    for name in list(env):
        if name in ("BASH_ENV", "ENV") or name.startswith("BASH_FUNC_"):
            del env[name]
    env["INCLUDE_ONLY"] = "1"
    env["LC_ALL"] = "C"
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        argv, input=input_text, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        universal_newlines=True, env=env, cwd=str(ROOT), timeout=10,
    )


# Events use tab-delimited arguments, preserving spaces, empty arguments and
# shell metacharacters. Test inputs deliberately contain no tabs/newlines.
SHELL_MOCKS = r'''
record() {
    local tag="$1" item
    shift
    printf '%s' "$tag"
    for item in "$@"; do printf '\t%s' "$item"; done
    printf '\n'
}
resolveip() {
    record_resolve="$*"
    case "$*" in
        '-4 -t 5 primary.example') printf '198.51.100.10\n198.51.100.11\n' ;;
        '-4 -t 5 backup.example') return 1 ;;
        '-4 -t 5 203.0.113.8') printf '203.0.113.8\n' ;;
        *) return 1 ;;
    esac
}
logger() { record logger "$@"; }
proto_add_host_dependency() { record dependency "$@"; }
'''

SETUP_MOCKS = SHELL_MOCKS + r'''
# Override only the script's two system-file probes, not numeric/string tests.
function [ {
    if builtin [ "$#" -eq 3 ]; then
        if builtin [ "$1" = -x ] && builtin [ "$2" = /usr/bin/n2n-edge ]; then
            builtin [ "${CFG_missing_binary:-0}" != 1 ]; return
        fi
        if builtin [ "$1" = -c ] && builtin [ "$2" = /dev/net/tun ]; then
            builtin [ "${CFG_missing_tun:-0}" != 1 ]; return
        fi
    fi
    builtin [ "$@"
}
json_get_vars() {
    local field env_name
    for field in "$@"; do
        env_name="CFG_$field"
        printf -v "$field" '%s' "${!env_name}"
    done
}
json_get_values() {
    local env_name="CFG_$2"
    printf -v "$1" '%s' "${!env_name}"
}
proto_notify_error() { record error "$@"; }
proto_block_restart() { record block "$@"; }
proto_export() { record export "$@"; }
proto_run_command() {
    record command "$@"
    command_started=1
    [ "${CFG_command_failure:-0}" != 1 ]
}
proto_kill_command() { record kill "$@"; }
proto_setup_failed() { record failed "$@"; }
proto_init_update() { record update "$@"; }
proto_add_ipv4_address() { record address "$@"; }
proto_add_ipv4_route() { record route "$@"; }
proto_send_update() { record send "$@"; }
proto_add_dynamic_defaults() { record dynamic_defaults; }
ip() {
    if [ "$*" = "link show dev $tunname" ]; then
        if [ "${CFG_device_in_use:-0}" = 1 ]; then
            printf '7: %s: <UP>\n' "$tunname"; return 0
        fi
        [ "${command_started:-0}" = 1 ] || return 1
        [ "${CFG_tap_timeout:-0}" != 1 ] || return 1
        printf '7: %s: <BROADCAST,MULTICAST,UP>\n' "$tunname"
        return 0
    fi
    if [ "$*" = "-4 -o address show dev $tunname" ]; then
        [ "${CFG_tap_timeout:-0}" != 1 ] || return 1
        if [ "$mode" = static ]; then
            printf '7: %s inet %s/%s scope global %s\n' "$tunname" "$ipaddr" "$prefix" "$tunname"
        fi
        return 0
    fi
    record unexpected_ip "$@"
    return 99
}
sleep() { record sleep "$@"; }
uci() {
    [ "$*" = "-q get network.${config}_4" ] || return 99
    [ "${CFG_uci_conflict:-0}" = 1 ]
}
ubus() {
    if [ "$*" = "-S list network.interface.${config}_4" ]; then
        [ "${CFG_ubus_conflict:-0}" != 1 ] || printf 'network.interface.%s_4\n' "$config"
        return 0
    fi
    if [ "$1" = call ] && [ "$2" = network ] && [ "$3" = add_dynamic ]; then
        record ubus "$@"
        [ "${CFG_dhcp_failure:-0}" != 1 ]
        return
    fi
    record unexpected_ubus "$@"
    return 99
}
config_load() { record config_load "$@"; }
config_foreach() { "$1" mock_zone; }
config_get() {
    case "$3" in
        network) printf -v "$1" '%s' "lan $config" ;;
        name) printf -v "$1" '%s' vpn ;;
        *) return 99 ;;
    esac
}
json_init() { record json_init; }
json_add_string() { record json_string "$@"; }
json_add_array() { record json_array "$@"; }
json_close_array() { record json_close_array; }
# A sentinel payload makes it observable that add_dynamic uses json_dump.
# Individual fields/defaults/array operations are checked in the event stream.
json_dump() { printf '{"mock":"dynamic"}'; }
'''


class ShellHelpersTest(unittest.TestCase):
    def shell(self, body, args=(), mocks=""):
        return run_process(
            ["/bin/sh", "-c", '. "$1"\nshift\n' + mocks + body,
             "n2n-test", str(SHELL_SOURCE)] + list(args)
        )

    def test_source_include_only_has_no_side_effects(self):
        result = self.shell(":")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "")

    def test_uint_ranges_and_syntax(self):
        cases = [
            ("0", 0, 65535, True), ("65535", 0, 65535, True),
            ("65536", 0, 65535, False), ("1", 1, 5, True),
            ("5", 1, 5, True), ("0", 1, 5, False), ("6", 1, 5, False),
            ("32", 0, 32, True), ("33", 0, 32, False),
            ("576", 576, 1500, True), ("1500", 576, 1500, True),
            ("575", 576, 1500, False), ("1501", 576, 1500, False),
            ("00001", 0, 65535, True), ("000001", 0, 65535, False),
            ("", 0, 65535, False), ("-1", 0, 65535, False),
            ("+1", 0, 65535, False), ("1.0", 0, 65535, False),
            (" 1", 0, 65535, False), ("1 ", 0, 65535, False),
            ("1e2", 0, 65535, False), ("999999999999999999", 0, 65535, False),
        ]
        for value, lower, upper, valid in cases:
            with self.subTest(value=value, lower=lower, upper=upper):
                result = self.shell('n2n_uint "$1" "$2" "$3"',
                                    (value, str(lower), str(upper)))
                self.assertEqual(result.returncode, 0 if valid else 1, result.stderr)

    def test_ipv4_octets_trailing_dot_and_leading_zero(self):
        valid = ["0.0.0.0", "255.255.255.255", "10.0.0.1", "192.168.1.254"]
        invalid = ["", "1.2.3", "1.2.3.4.5", "1.2.3.4.", ".1.2.3.4",
                   "1..2.3", "1.2.3.256", "256.2.3.4", "01.2.3.4",
                   "1.02.3.4", "1.2.003.4", "1.2.3.00", "1.2.3.-1",
                   "1.2.3.+1", "1.2.3.a", " 1.2.3.4", "1.2.3.4 ",
                   "1.2.3.4/24", "::1", "999999999999.2.3.4"]
        for address in valid + invalid:
            with self.subTest(address=address):
                result = self.shell('n2n_ipv4 "$1"', (address,))
                self.assertEqual(result.returncode, 0 if address in valid else 1,
                                 result.stderr)

    def test_supernode_resolves_multiple_addresses_and_uplink(self):
        result = self.shell(
            'config=vpn; tunlink=wan; n2n_add_supernode "$1"',
            ("primary.example:1234",), SHELL_MOCKS)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), [
            "dependency\tvpn\t198.51.100.10\twan",
            "dependency\tvpn\t198.51.100.11\twan",
        ])

    def test_supernode_literal_and_empty_uplink(self):
        result = self.shell(
            'config=vpn; tunlink=""; n2n_add_supernode "$1"',
            ("203.0.113.8:65535",), SHELL_MOCKS)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "dependency\tvpn\t203.0.113.8\t\n")

    def test_unresolved_backup_is_logged_without_failing_primary(self):
        result = self.shell(
            'config=vpn; tunlink=wan; n2n_add_supernode primary.example:1234 && '
            'n2n_add_supernode backup.example:1234', mocks=SHELL_MOCKS)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), [
            "dependency\tvpn\t198.51.100.10\twan",
            "dependency\tvpn\t198.51.100.11\twan",
            "logger\t-t\tn2n[vpn]\tCannot resolve supernode backup.example",
        ])

    def test_supernode_invalid_endpoint_rejected_before_resolution(self):
        invalid = ["", "primary.example", ":1234", "-bad:1234", ".bad:1234",
                   "bad host:1234", "host/path:1234", "host:0", "host:65536",
                   "host:-1", "host:+1", "host:123456", "host:abc", "host:",
                   "host:12:34", "[::1]:1234", "host:1;true"]
        # This override fails visibly if parsing accidentally reaches resolveip.
        mocks = SHELL_MOCKS + '\nresolveip() { printf "UNEXPECTED_RESOLVE\\n"; }\n'
        for endpoint in invalid:
            with self.subTest(endpoint=endpoint):
                result = self.shell(
                    'config=vpn; tunlink=wan; n2n_add_supernode "$1"',
                    (endpoint,), mocks)
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertEqual(result.stdout, "")


@unittest.skipUnless(BASH, "Bash is required to mock only '[' file checks")
class SetupTest(unittest.TestCase):
    def setup_run(self, config="vpn", **values):
        defaults = dict(supernode="primary.example:1234", community="test-community",
                        key="secret $key; with spaces", ipaddr="10.20.30.40")
        defaults.update(values)
        env = {"CFG_" + name: str(value) for name, value in defaults.items()}
        result = run_process(
            [BASH, "--noprofile", "--norc", "-c",
             '. "$1"\n' + SETUP_MOCKS +
             '\nproto_n2n_setup "$2"\nstatus=$?\nrecord status "$status"\n',
             "n2n-test", str(SHELL_SOURCE), config], extra_env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        events = [tuple(line.split("\t")) for line in result.stdout.splitlines()]
        self.assertFalse(any(event[0].startswith("unexpected_") for event in events), events)
        self.assertEqual(events[-1][0], "status")
        return events

    def tagged(self, events, tag):
        return [event[1:] for event in events if event[0] == tag]

    def assert_success(self, events):
        self.assertEqual(events[-1], ("status", "0"), events)
        self.assertEqual(self.tagged(events, "error"), [])
        self.assertEqual(self.tagged(events, "kill"), [])
        commands = self.tagged(events, "command")
        self.assertEqual(len(commands), 1, events)
        self.assertEqual(commands[0][:3], ("vpn", "/usr/bin/n2n-edge", "-f"))
        self.assertNotIn("/dev/null", commands[0])
        return commands[0][1:]

    def assert_rejected(self, expected, **values):
        events = self.setup_run(**values)
        self.assertEqual(events[-1], ("status", "1"), events)
        self.assertEqual(self.tagged(events, "error"), [("vpn", expected)])
        self.assertEqual(self.tagged(events, "block"), [("vpn",)])
        for tag in ("command", "update", "send", "ubus"):
            self.assertEqual(self.tagged(events, tag), [], events)

    def test_static_defaults_no_z0_external_update_and_secret_export(self):
        key = "secret $key; with spaces"
        events = self.setup_run(key=key)
        argv = self.assert_success(events)
        self.assertEqual(argv, (
            "/usr/bin/n2n-edge", "-f", "-d", "n2n-vpn",
            "-a", "static:10.20.30.40/24",
            "-M", "1290", "-p", "0", "-t", "0", "-A3",
            "-l", "primary.example:1234", "-r",
        ))
        self.assertFalse(any(arg.startswith("-z") for arg in argv))
        self.assertNotIn("-k", argv)
        self.assertFalse(any(key in arg for arg in argv))
        self.assertNotIn("-c", argv)
        self.assertNotIn("test-community", argv)
        self.assertEqual(self.tagged(events, "export"), [
            ("N2N_KEY=" + key,), ("N2N_COMMUNITY=test-community",),
        ])
        self.assertEqual(self.tagged(events, "update"), [("n2n-vpn", "1", "1")])
        self.assertEqual(self.tagged(events, "address"), [("10.20.30.40", "24")])
        self.assertEqual(self.tagged(events, "send"), [("vpn",)])
        self.assertEqual(self.tagged(events, "sleep"), [])
        self.assertEqual(self.tagged(events, "ubus"), [])

    def test_compression_flags_and_optional_command_arguments(self):
        for compression in ("0", "1", "2"):
            with self.subTest(compression=compression):
                events = self.setup_run(
                    compression=compression, tunname="tap_test", prefix="32",
                    mtu="1500", local_port="65535", management_port="12345",
                    cipher="5", routing="0", header_encryption="1", multicast="1",
                    macaddr="02:00:00:00:00:01")
                argv = self.assert_success(events)
                self.assertEqual([arg for arg in argv if arg.startswith("-z")],
                                 [] if compression == "0" else ["-z" + compression])
                for flag in ("-A5", "-H", "-E"):
                    self.assertIn(flag, argv)
                self.assertNotIn("-r", argv)
                for flag, value in (("-d", "tap_test"), ("-p", "65535"),
                                    ("-t", "12345"), ("-M", "1500"),
                                    ("-a", "static:10.20.30.40/32"),
                                    ("-m", "02:00:00:00:00:01")):
                    self.assertEqual(argv[argv.index(flag) + 1], value)

    def test_uplink_dependencies_and_unresolved_backup_still_start(self):
        events = self.setup_run(tunlink="wan", supernode=
                                "primary.example:1234 backup.example:4321")
        argv = self.assert_success(events)
        self.assertEqual(self.tagged(events, "dependency"), [
            ("vpn", "", "wan"), ("vpn", "198.51.100.10", "wan"),
            ("vpn", "198.51.100.11", "wan"),
        ])
        self.assertIn("backup.example:4321", argv)
        self.assertEqual(self.tagged(events, "logger"), [
            ("-t", "n2n[vpn]", "Cannot resolve supernode backup.example")])

    def test_gateway_and_defaultroute(self):
        for enabled in ("0", "1"):
            with self.subTest(defaultroute=enabled):
                events = self.setup_run(gateway="10.20.30.1", defaultroute=enabled)
                self.assert_success(events)
                self.assertEqual(self.tagged(events, "route"), [] if enabled == "0" else
                                 [("0.0.0.0", "0", "10.20.30.1")])

    def test_dhcp_adds_dynamic_alias_without_replacing_edge(self):
        events = self.setup_run(mode="dhcp", routing="0", hostname="overlay-client",
                                ip4table="100", dns="1.1.1.1 8.8.8.8")
        argv = self.assert_success(events)
        self.assertIn("dhcp:0.0.0.0/24", argv)
        self.assertIn("-r", argv)
        self.assertEqual(self.tagged(events, "update"), [("n2n-vpn", "1", "1")])
        self.assertEqual(self.tagged(events, "address"), [])
        self.assertEqual(self.tagged(events, "ubus"), [
            ("call", "network", "add_dynamic", '{"mock":"dynamic"}')])
        self.assertEqual(self.tagged(events, "json_string"), [
            ("name", "vpn_4"), ("ifname", "@vpn"), ("proto", "dhcp"),
            ("hostname", "overlay-client"), ("zone", "vpn"), ("ip4table", "100"),
            ("", "1.1.1.1"), ("", "8.8.8.8"),
        ])
        self.assertEqual(self.tagged(events, "dynamic_defaults"), [()])
        self.assertEqual(self.tagged(events, "json_array"), [("dns",)])
        self.assertEqual(self.tagged(events, "json_close_array"), [()])
        self.assertLess(events.index(("send", "vpn")),
                        events.index(("ubus", "call", "network", "add_dynamic",
                                      '{"mock":"dynamic"}')))

    def test_dhcp_existing_uci_or_ubus_alias_rejected(self):
        for conflict in ("uci_conflict", "ubus_conflict"):
            with self.subTest(conflict=conflict):
                self.assert_rejected("DHCP_INTERFACE_IN_USE", mode="dhcp",
                                     **{conflict: "1"})

    def test_missing_system_prerequisites_are_mocked(self):
        self.assert_rejected("MISSING_BINARY", missing_binary="1")
        self.assert_rejected("MISSING_TUN", missing_tun="1")

    def test_invalid_devices_and_uplink(self):
        for name in ("x" * 16, "bad.name", "bad name", "tap/other"):
            with self.subTest(name=name):
                self.assert_rejected("INVALID_DEVICE", tunname=name)
        events = self.setup_run(config="long_interface_name")
        self.assertIn(("error", "long_interface_name", "INVALID_DEVICE"), events)
        self.assertEqual(self.tagged(events, "command"), [])
        self.assert_rejected("INVALID_UPLINK", tunlink="vpn")
        self.assert_rejected("DEVICE_IN_USE", device_in_use="1")

    def test_invalid_configuration_rejected(self):
        cases = [
            ("INVALID_CONFIG", {"supernode": ""}),
            ("INVALID_CONFIG", {"community": ""}),
            ("INVALID_CONFIG", {"community": "a" * 16}),
            ("INVALID_ADDRESS", {"ipaddr": "0.0.0.0"}),
            ("INVALID_ADDRESS", {"ipaddr": "10.20.30.40."}),
            ("INVALID_ADDRESS", {"ipaddr": "010.20.30.40"}),
            ("INVALID_ADDRESS", {"ipaddr": "10.20.30.256"}),
            ("INVALID_GATEWAY", {"gateway": "10.20.30.1."}),
            ("INVALID_MODE", {"mode": "external"}),
            ("INVALID_KEY", {"key": ""}),
            ("INVALID_KEY", {"key": "test-community"}),
            ("KEY_WITHOUT_ENCRYPTION", {"cipher": "1"}),
            ("INVALID_SUPERNODE", {"supernode": "primary.example:0"}),
        ]
        for error, values in cases:
            with self.subTest(error=error, values=values):
                self.assert_rejected(error, **values)

    def test_invalid_numeric_ranges(self):
        cases = {"prefix": ("-1", "33", "24x"), "mtu": ("575", "1501"),
                 "local_port": ("-1", "65536"), "management_port": ("-1", "65536"),
                 "cipher": ("0", "6"), "compression": ("-1", "3"),
                 "startup_timeout": ("4", "121", "999999")}
        for field, values in cases.items():
            for value in values:
                with self.subTest(field=field, value=value):
                    self.assert_rejected("INVALID_CONFIG", **{field: value})

    def test_unencrypted_mode_exports_only_community(self):
        events = self.setup_run(cipher="1", key="")
        argv = self.assert_success(events)
        self.assertIn("-A1", argv)
        self.assertNotIn("-c", argv)
        self.assertNotIn("test-community", argv)
        self.assertEqual(self.tagged(events, "export"), [("N2N_COMMUNITY=test-community",)])

    def test_sensitive_values_are_preserved_only_in_command_environment(self):
        community = "group $x;'\""
        key = "secret $key; with spaces and 'quotes'"
        for mode in ("static", "dhcp"):
            with self.subTest(mode=mode):
                events = self.setup_run(mode=mode, community=community, key=key)
                argv = self.assert_success(events)
                self.assertNotIn("-c", argv)
                self.assertNotIn("-k", argv)
                self.assertFalse(any(community in arg or key in arg for arg in argv))
                self.assertEqual(self.tagged(events, "export"), [
                    ("N2N_KEY=" + key,), ("N2N_COMMUNITY=" + community,),
                ])
                self.assertEqual(self.tagged(events, "logger"), [])

    def test_tap_timeout_is_bounded_and_cleans_up(self):
        for mode in ("static", "dhcp"):
            with self.subTest(mode=mode):
                events = self.setup_run(mode=mode, tap_timeout="1", startup_timeout="5")
                self.assertEqual(events[-1], ("status", "1"))
                self.assertEqual(self.tagged(events, "sleep"), [("1",)] * 5)
                self.assertEqual(self.tagged(events, "error"), [("vpn", "TAP_TIMEOUT")])
                self.assertEqual(self.tagged(events, "kill"), [("vpn",)])
                self.assertEqual(self.tagged(events, "failed"), [("vpn",)])
                self.assertEqual(self.tagged(events, "update"), [])
                self.assertEqual(self.tagged(events, "ubus"), [])

    def test_dhcp_add_dynamic_failure_cleans_up_edge(self):
        events = self.setup_run(mode="dhcp", dhcp_failure="1")
        self.assertEqual(events[-1], ("status", "1"))
        self.assertEqual(len(self.tagged(events, "command")), 1)
        self.assertEqual(self.tagged(events, "error"), [("vpn", "DHCP_FAILED")])
        self.assertEqual(self.tagged(events, "kill"), [("vpn",)])
        self.assertEqual(self.tagged(events, "failed"), [("vpn",)])

    def test_command_failure_does_not_publish_interface(self):
        events = self.setup_run(command_failure="1")
        self.assertEqual(events[-1], ("status", "1"))
        for tag in ("update", "send", "ubus", "sleep"):
            self.assertEqual(self.tagged(events, tag), [])

    def test_teardown_only_kills_parent_command(self):
        result = run_process([BASH, "--noprofile", "--norc", "-c",
                              '. "$1"\n' + SETUP_MOCKS + '\nproto_n2n_teardown vpn',
                              "n2n-test", str(SHELL_SOURCE)])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "kill\tvpn\n")


NODE_MOCKS = r'''
const fs = require('fs');
const vm = require('vm');
const { TextEncoder } = require('util');
const request = JSON.parse(fs.readFileSync(0, 'utf8'));
const options = Object.create(null);
const dynamicList = {
    prototype: {
        parse: function(sid) {
            return this.isActive(sid) ? Promise.resolve(this.formvalue(sid)) : Promise.resolve();
        }
    }
};
const context = vm.createContext({
    TextEncoder,
    _: value => value,
    form: { DynamicList: dynamicList, Value: 'Value', ListValue: 'ListValue', Flag: 'Flag' },
    widgets: { NetworkSelect: 'NetworkSelect' },
    uci: {
        sections: (config, type, callback) => {
            if (config !== 'network' || type !== 'interface') throw Error('Unexpected UCI query');
            (request.sections || []).forEach(callback);
        }
    },
    network: {
        registerPatternVirtual: pattern => {
            if (!pattern.test('n2n-vpn') || pattern.test('eth0')) throw Error('Bad virtual pattern');
        },
        registerProtocol: (name, protocol) => {
            if (name !== 'n2n') throw Error('Wrong protocol');
            return protocol;
        },
        getIfnameOf: name => name
    }
});
vm.runInContext("String.prototype.format = function(value) { return this.replace('%s', value); };", context);
const source = fs.readFileSync(process.argv[1], 'utf8');
// LuCI modules contain a top-level return, so evaluate in a function as LuCI does.
const protocol = vm.runInContext('(function() {\n' + source + '\n})()', context, { timeout: 1000 });
protocol.sid = request.sid || 'vpn';
protocol._ubus = () => request.l3_device || null;
protocol.get = name => (request.values || {})[name];
protocol.renderFormOptions({
    taboption: (tab, type, name) => {
        const option = {
            tab, type: type === dynamicList ? 'DynamicList' : type,
            enabled: '1', choices: [], dependencies: [],
            formvalue: () => (request.values || {})[name] ?? null,
            isActive: () => request.active !== false,
            value: function(value, label) { this.choices.push([value, label]); },
            depends: function(key, value) { this.dependencies.push([key, value]); }
        };
        options[name] = option;
        return option;
    },
    formvalue: (sid, name) => (request.values || {})[name]
});
const validations = (request.checks || []).map(check => {
    if (!options[check.field] || typeof options[check.field].validate !== 'function')
        throw Error('Missing validator: ' + check.field);
    return options[check.field].validate(check.sid || protocol.sid, check.value);
});
const metadata = {};
for (const [name, option] of Object.entries(options)) {
    metadata[name] = {};
    for (const [key, value] of Object.entries(option))
        if (typeof value !== 'function') metadata[name][key] = value;
}
async function finish() {
    const parses = [];
    for (const field of request.parse || []) {
        try {
            await options[field].parse(protocol.sid);
            parses.push(true);
        }
        catch (error) {
            parses.push(error.message);
        }
    }
    process.stdout.write(JSON.stringify({
        validations, parses, options: metadata,
        protocol: {
            ifname: protocol.getIfname(), package: protocol.getPackageName(),
            floating: protocol.isFloating(), virtual: protocol.isVirtual(),
            devices: protocol.getDevices(), contains: protocol.containsDevice(protocol.getIfname())
        }
    }));
}
finish().catch(error => { console.error(error); process.exitCode = 1; });
'''


@unittest.skipUnless(NODE, "Node is optional and required for LuCI VM tests")
class FrontendTest(unittest.TestCase):
    def evaluate(self, **request):
        result = run_process([NODE, "-e", NODE_MOCKS, str(FRONTEND_SOURCE)],
                             input_text=json.dumps(request))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        return json.loads(result.stdout)

    def check_values(self, field, valid, invalid, **request):
        checks = [{"field": field, "value": value} for value in valid + invalid]
        results = self.evaluate(checks=checks, **request)["validations"]
        for value, result in zip(valid + invalid, results):
            with self.subTest(field=field, value=value):
                if value in valid:
                    self.assertIs(result, True)
                else:
                    self.assertIsInstance(result, str)
                    self.assertTrue(result)

    def test_node_syntax(self):
        result = run_process([NODE, "--check", str(FRONTEND_SOURCE)])
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_supernode_validator(self):
        self.check_values("supernode",
                          ["vpn.example.com:1234", "203.0.113.8:1", "host:65535"],
                          ["host", ":1234", "host:0", "host:65536", "host:123456",
                           "host:-1", "host:+1", "host:abc", "host:12:34",
                           "-host:1234", ".host:1234", "bad host:1234", "[::1]:1234"])

    def test_supernode_dynamic_list_blank_input_and_array_validation(self):
        # LuCI validates both the blank add-item input and the list container.
        # Neither is a missing supernode when the list already contains items.
        self.check_values("supernode", ["", [], ["host:1234"],
                          ["host:1234", "backup:65535"]],
                          [["host:1234", "backup:0"], [""], ["host"]])
        option = self.evaluate()["options"]["supernode"]
        self.assertTrue(option["optional"])
        self.assertFalse(option["rmempty"])

    def test_supernode_save_requires_nonempty_valid_list(self):
        for values, valid in [(["host:1234"], True),
                              (["host:1234", "backup:65535"], True),
                              ([], False), (None, False),
                              (["host:1234", "backup:0"], False),
                              ([""], False)]:
            with self.subTest(values=values):
                result = self.evaluate(values={"supernode": values},
                                       parse=["supernode"])["parses"][0]
                if valid:
                    self.assertIs(result, True)
                else:
                    self.assertIsInstance(result, str)
                    self.assertIn("Supernodes", result)
        self.assertIs(self.evaluate(active=False, parse=["supernode"])["parses"][0], True)

    def test_community_byte_length_validator(self):
        self.check_values("community", ["a", "a" * 15, "中文社区一"],
                          ["", "a" * 16, "中文社区一个"])

    def test_key_validator_and_password_metadata(self):
        self.check_values("key", ["different key", "secret;$value"],
                          ["", "community"], values={"community": "community"})
        option = self.evaluate()["options"]["key"]
        self.assertTrue(option["password"])
        self.assertEqual(option["dependencies"], [["cipher", str(n)] for n in range(2, 6)])

    def test_ipv4_validator(self):
        self.check_values("ipaddr", ["1.2.3.4", "255.255.255.255", "0.0.0.1"],
                          ["", "0.0.0.0", "1.2.3.4.", ".1.2.3.4", "1.2.3",
                           "1.2.3.4.5", "01.2.3.4", "1.02.3.4", "1.2.3.00",
                           "1.2.3.256", "1.2.3.-1", "1.2.3.a", "1.2.3.4/24"])

    def test_tap_name_validator_and_duplicate_detection(self):
        sections = [
            {".name": "other", "proto": "n2n", "tunname": "used_tap"},
            {".name": "backup", "proto": "n2n"},
            {".name": "vpn", "proto": "n2n", "tunname": "own_tap"},
            {".name": "lan", "proto": "static", "tunname": "not_n2n"},
        ]
        self.check_values("tunname", ["", "a" * 15, "tap_1-2", "own_tap", "not_n2n"],
                          ["a" * 16, "tap.name", "tap/name", "tap name", "used_tap", "n2n-backup"],
                          sections=sections)
        self.check_values("tunname", ["short_tap"], [""], sid="long_interface_name")

    def test_uplink_validator(self):
        self.check_values("tunlink", ["", "wan", "lan"], ["vpn"])
        self.assertEqual(self.evaluate()["options"]["tunlink"]["exclude"], "vpn")

    def test_management_port_validator_and_duplicate_detection(self):
        sections = [
            {".name": "other", "proto": "n2n", "management_port": "1234"},
            {".name": "vpn", "proto": "n2n", "management_port": "4321"},
            {".name": "lan", "proto": "static", "management_port": "5678"},
        ]
        self.check_values("management_port", ["", "0", "00000", "65535", "4321", "5678"],
                          ["1234", "01234", "65536", "-1", "+1", "1.0", "1e2", "000000"],
                          sections=sections)

    def test_form_defaults_choices_and_datatypes(self):
        options = self.evaluate()["options"]
        for name, datatype in {
            "prefix": "range(0,32)", "mtu": "range(576,1500)",
            "local_port": "range(0,65535)", "management_port": "range(0,65535)",
            "startup_timeout": "range(5,120)", "gateway": 'ip4addr("nomask")',
        }.items():
            with self.subTest(option=name):
                self.assertEqual(options[name]["datatype"], datatype)
        self.assertEqual(options["cipher"]["default"], "3")
        self.assertEqual(options["compression"]["tab"], "general")
        self.assertEqual(options["compression"]["default"], "0")
        self.assertFalse(options["compression"]["rmempty"])
        self.assertEqual(options["compression"]["choices"],
                         [["0", "None"], ["1", "LZO"], ["2", "Zstandard"]])
        self.assertEqual(options["mode"]["default"], "static")
        self.assertEqual([choice[0] for choice in options["mode"]["choices"]], ["static", "dhcp"])
        self.assertEqual(options["management_port"]["placeholder"], "0")
        self.assertEqual(options["ipaddr"]["dependencies"], [["mode", "static"]])
        self.assertEqual(options["hostname"]["dependencies"], [["mode", "dhcp"]])

    def test_protocol_device_selection(self):
        for request, expected in (({}, "n2n-vpn"),
                                  ({"values": {"tunname": "custom_tap"}}, "custom_tap"),
                                  ({"l3_device": "ubus_tap", "values": {"tunname": "custom_tap"}},
                                   "ubus_tap")):
            with self.subTest(request=request):
                protocol = self.evaluate(**request)["protocol"]
                self.assertEqual(protocol["ifname"], expected)
                self.assertEqual(protocol["package"], "luci-proto-n2n")
                self.assertTrue(protocol["floating"])
                self.assertTrue(protocol["virtual"])
                self.assertTrue(protocol["contains"])
                self.assertIsNone(protocol["devices"])


class PackageDependenciesTest(unittest.TestCase):
    def test_ip_dependency_accepts_tiny_and_full_providers(self):
        makefile = (ROOT / "luci-proto-n2n/Makefile").read_text(encoding="utf-8")
        dependencies = re.search(r"^LUCI_DEPENDS:=(.*)$", makefile, re.MULTILINE).group(1).split()
        self.assertIn("+ip", dependencies)
        self.assertNotIn("+ip-tiny", dependencies)
        self.assertNotIn("+ip-full", dependencies)

    def test_edge_openssl_links_only_crypto(self):
        backend = ROOT / "net/n2n-edge"
        makefile = (backend / "Makefile").read_text(encoding="utf-8")
        self.assertIn("-DN2N_OPTION_USE_OPENSSL=ON", makefile)
        self.assertIn("+libopenssl", makefile)
        additions = "\n".join(
            line[1:]
            for patch in (backend / "patches").glob("*.patch")
            for line in patch.read_text(encoding="utf-8").splitlines()
            if line.startswith("+") and not line.startswith("+++")
        )
        self.assertIn("target_link_libraries(n2n OpenSSL::Crypto)", additions)
        self.assertNotIn("${OPENSSL_LIBRARIES}", additions)
        self.assertNotIn("OpenSSL::SSL", additions)


class TranslationTest(unittest.TestCase):
    def test_simplified_chinese_covers_all_frontend_strings(self):
        source = FRONTEND_SOURCE.read_text(encoding="utf-8")
        messages = {
            ast.literal_eval("'" + match + "'")
            for match in re.findall(r"_\('((?:\\.|[^'\\])*)'\)", source)
        }
        po = ROOT / "luci-proto-n2n/po/zh_Hans/n2n.po"
        catalog = {}
        for entry in po.read_text(encoding="utf-8").strip().split("\n\n"):
            fields = {}
            current = None
            for line in entry.splitlines():
                self.assertFalse(line.startswith("#, fuzzy"), "Unreviewed translation")
                if line.startswith("msgid "):
                    current = "msgid"
                    fields[current] = ast.literal_eval(line[6:])
                elif line.startswith("msgstr "):
                    current = "msgstr"
                    fields[current] = ast.literal_eval(line[7:])
                elif line.startswith('"'):
                    fields[current] += ast.literal_eval(line)
                else:
                    self.assertTrue(line.startswith("#"), "Invalid PO line: " + line)
            self.assertIn("msgid", fields)
            self.assertIn("msgstr", fields)
            self.assertNotIn(fields["msgid"], catalog, "Duplicate translation")
            self.assertTrue(fields["msgstr"], "Empty translation: " + fields["msgid"])
            catalog[fields["msgid"]] = fields["msgstr"]
        self.assertIn("Language: zh_Hans\n", catalog[""])
        self.assertIn("charset=UTF-8", catalog[""])
        self.assertEqual(messages, set(catalog) - {""})


@unittest.skipUnless(BASH, "Bash is required for the release collection step")
class ReleasePackagesTest(unittest.TestCase):
    def collection_script(self):
        workflow = (ROOT / ".github/workflows/release-openwrt.yml").read_text(encoding="utf-8")
        step = workflow.split("      - name: Collect project packages\n", 1)[1]
        return textwrap.dedent(step.split("        run: |\n", 1)[1].split("\n      - name:", 1)[0])

    def test_collection_includes_chinese_and_checksums_for_ipk_and_apk(self):
        for extension in ("ipk", "apk"):
            with self.subTest(extension=extension), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                packages = root / "bin/packages/x86_64/action"
                packages.mkdir(parents=True)
                names = ("n2n-edge", "luci-proto-n2n", "luci-i18n-n2n-zh-cn")
                filenames = [
                    name + ("_1.0_all.ipk" if extension == "ipk" else "-1.0.apk")
                    for name in names
                ]
                for filename in filenames:
                    (packages / filename).write_text(filename, encoding="utf-8")
                (packages / ("libopenssl." + extension)).write_text("dependency", encoding="utf-8")
                result = subprocess.run(
                    [BASH, "-c", self.collection_script()], cwd=directory,
                    env=dict(os.environ, ARCH="x86_64", SDK="25.12.5"),
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, timeout=10,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                release = root / "release"
                expected = {
                    filename.rsplit(".", 1)[0] + "_openwrt-25.12.5_x86_64." + extension
                    for filename in filenames
                }
                self.assertEqual({path.name for path in release.glob("*." + extension)}, expected)
                checksums = (release / "sha256sums_openwrt-25.12.5_x86_64").read_text().splitlines()
                self.assertEqual(len(checksums), 3)
                for line in checksums:
                    checksum, filename = line.split("  ", 1)
                    self.assertIn(filename, expected)
                    self.assertEqual(checksum, hashlib.sha256((release / filename).read_bytes()).hexdigest())

    def test_collection_rejects_missing_chinese_package(self):
        with tempfile.TemporaryDirectory() as directory:
            packages = Path(directory) / "bin/packages/x86_64/action"
            packages.mkdir(parents=True)
            for name in ("n2n-edge", "luci-proto-n2n"):
                (packages / (name + "_1.0_all.ipk")).write_text(name, encoding="utf-8")
            result = subprocess.run(
                [BASH, "-c", self.collection_script()], cwd=directory,
                env=dict(os.environ, ARCH="x86_64", SDK="25.12.5"),
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, timeout=10,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("luci-i18n-n2n-zh-cn", result.stdout)
            self.assertFalse((Path(directory) / "release").exists())


if __name__ == "__main__":
    unittest.main()
