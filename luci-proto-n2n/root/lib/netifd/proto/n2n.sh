#!/bin/sh
# SPDX-License-Identifier: GPL-3.0-only

[ -n "$INCLUDE_ONLY" ] || {
	. /lib/functions.sh
	. ../netifd-proto.sh
	init_proto "$@"
}

proto_n2n_init_config() {
	no_device=1
	available=1
	proto_config_add_array 'supernode:list(string)'
	proto_config_add_string community
	proto_config_add_string key
	proto_config_add_string mode
	proto_config_add_string 'ipaddr:ip4addr'
	proto_config_add_int prefix
	proto_config_add_string 'gateway:ip4addr'
	proto_config_add_string tunname
	proto_config_add_string tunlink
	proto_config_add_string 'macaddr:macaddr'
	proto_config_add_int mtu
	proto_config_add_int local_port
	proto_config_add_int management_port
	proto_config_add_int cipher
	proto_config_add_int compression
	proto_config_add_int startup_timeout
	proto_config_add_boolean routing
	proto_config_add_boolean header_encryption
	proto_config_add_boolean multicast
	proto_config_add_boolean peerdns
	proto_config_add_string hostname
	proto_config_add_string ip4table
	proto_config_add_array 'dns:list(ipaddr)'
}

n2n_uint() {
	case "$1" in ''|*[!0-9]*) return 1 ;; esac
	[ ${#1} -le 5 ] && [ "$1" -ge "$2" ] && [ "$1" -le "$3" ]
}

n2n_ipv4() {
	local address="$1" octet
	case "$address" in ''|*[!0-9.]*|.*|*.|*..*) return 1 ;; esac
	local IFS=.
	# The address has been restricted to digits and dots; split its four octets.
	# shellcheck disable=SC2086
	set -- $address
	[ "$#" = 4 ] || return 1
	for octet in "$@"; do
		case "$octet" in 0?*) return 1 ;; esac
		n2n_uint "$octet" 0 255 || return 1
	done
}

n2n_error() {
	proto_notify_error "$1" "$2"
	proto_block_restart "$1"
	return 1
}

# Only resolve the underlay hosts, never route supernodes through the overlay.
n2n_add_supernode() {
	local endpoint="$1" host port addresses address
	case "$endpoint" in *:*) ;; *) return 1 ;; esac
	host="${endpoint%:*}"
	port="${endpoint##*:}"
	case "$host" in ''|*[!a-zA-Z0-9.-]*|.*|-*) return 1 ;; esac
	n2n_uint "$port" 1 65535 || return 1
	addresses="$(resolveip -4 -t 5 "$host" 2>/dev/null)"
	if [ -z "$addresses" ]; then
		# One unavailable backup must not prevent using other supernodes.
		logger -t "n2n[$config]" "Cannot resolve supernode $host"
		return 0
	fi
	for address in $addresses; do
		proto_add_host_dependency "$config" "$address" "$tunlink"
	done
	return 0
}

n2n_find_zone() {
	local section="$1" networks name member
	config_get networks "$section" network
	config_get name "$section" name
	for member in $networks; do
		[ "$member" = "$config" ] && zone="$name"
	done
}

proto_n2n_setup() {
	local config="$1"
	local supernode community key mode ipaddr prefix gateway tunname tunlink
	local macaddr mtu local_port management_port cipher compression startup_timeout
	local routing header_encryption multicast defaultroute peerdns metric hostname ip4table dns
	local endpoint elapsed zone
	json_get_vars community key mode ipaddr prefix gateway tunname tunlink macaddr mtu \
		local_port management_port cipher compression startup_timeout routing \
		header_encryption multicast defaultroute peerdns metric hostname ip4table
	json_get_values supernode supernode
	json_get_values dns dns

	[ -x /usr/bin/n2n-edge ] || { n2n_error "$config" MISSING_BINARY; return 1; }
	[ -c /dev/net/tun ] || { n2n_error "$config" MISSING_TUN; return 1; }
	if [ -z "$supernode" ] || [ -z "$community" ] || [ ${#community} -gt 15 ]; then
		n2n_error "$config" INVALID_CONFIG
		return 1
	fi
	mode="${mode:-static}"
	prefix="${prefix:-24}"
	mtu="${mtu:-1290}"
	local_port="${local_port:-0}"
	# Port zero requests an ephemeral loopback management socket per instance.
	management_port="${management_port:-0}"
	cipher="${cipher:-3}"
	compression="${compression:-0}"
	startup_timeout="${startup_timeout:-30}"
	tunname="${tunname:-n2n-$config}"
	case "$tunname" in ''|*[!a-zA-Z0-9_-]*) n2n_error "$config" INVALID_DEVICE; return 1 ;; esac
	[ ${#tunname} -le 15 ] || { n2n_error "$config" INVALID_DEVICE; return 1; }
	[ "$tunlink" != "$config" ] || { n2n_error "$config" INVALID_UPLINK; return 1; }
	if ip link show dev "$tunname" >/dev/null 2>&1; then
		n2n_error "$config" DEVICE_IN_USE
		return 1
	fi
	if ! n2n_uint "$prefix" 0 32 || ! n2n_uint "$mtu" 576 1500 || \
		! n2n_uint "$local_port" 0 65535 || ! n2n_uint "$management_port" 0 65535 || \
		! n2n_uint "$cipher" 1 5 || ! n2n_uint "$compression" 0 2 || \
		! n2n_uint "$startup_timeout" 5 120; then
		n2n_error "$config" INVALID_CONFIG
		return 1
	fi
	case "$mode" in
		static)
			if ! n2n_ipv4 "$ipaddr" || [ "$ipaddr" = 0.0.0.0 ]; then
				n2n_error "$config" INVALID_ADDRESS
				return 1
			fi
			[ -z "$gateway" ] || n2n_ipv4 "$gateway" || {
				n2n_error "$config" INVALID_GATEWAY
				return 1
			}
			;;
		dhcp)
			ipaddr=0.0.0.0
			# add_dynamic replaces existing names, so never overwrite an interface.
			if uci -q get "network.${config}_4" >/dev/null 2>&1 || \
				ubus -S list "network.interface.${config}_4" | grep -q .; then
				n2n_error "$config" DHCP_INTERFACE_IN_USE
				return 1
			fi
			;;
		*) n2n_error "$config" INVALID_MODE; return 1 ;;
	esac
	if [ "$cipher" = 1 ]; then
		[ -z "$key" ] || { n2n_error "$config" KEY_WITHOUT_ENCRYPTION; return 1; }
	else
		if [ -z "$key" ] || [ "$key" = "$community" ]; then
			n2n_error "$config" INVALID_KEY
			return 1
		fi
		# Keep the key out of the command line, temporary files and logs.
		proto_export "N2N_KEY=$key"
	fi
	[ -z "$tunlink" ] || proto_add_host_dependency "$config" '' "$tunlink"
	for endpoint in $supernode; do
		n2n_add_supernode "$endpoint" || { n2n_error "$config" INVALID_SUPERNODE; return 1; }
	done

	# Pass sensitive values through netifd's command environment, never argv.
	proto_export "N2N_COMMUNITY=$community"
	set -- /usr/bin/n2n-edge -f -d "$tunname" \
		-a "$mode:$ipaddr/$prefix" -M "$mtu" -p "$local_port" \
		-t "$management_port" "-A$cipher"
	# N2N 3.0 rejects -z0; no compression is selected by omitting the option.
	[ "$compression" = 0 ] || set -- "$@" "-z$compression"
	for endpoint in $supernode; do set -- "$@" -l "$endpoint"; done
	[ -z "$macaddr" ] || set -- "$@" -m "$macaddr"
	if [ "${routing:-1}" = 1 ] || [ "$mode" = dhcp ]; then
		set -- "$@" -r
	fi
	[ "$header_encryption" = 1 ] && set -- "$@" -H
	[ "$multicast" = 1 ] && set -- "$@" -E
	proto_run_command "$config" "$@" || return 1

	# edge has no up-script hook. Wait for its configured TAP, with a hard limit.
	elapsed=0
	while [ "$elapsed" -lt "$startup_timeout" ]; do
		if ip -4 -o address show dev "$tunname" 2>/dev/null | grep -Fq " inet $ipaddr/$prefix "; then
			break
		fi
		if [ "$mode" = dhcp ] && ip link show dev "$tunname" 2>/dev/null | grep -q '<[^>]*UP'; then
			break
		fi
		sleep 1
		elapsed=$((elapsed + 1))
	done
	if [ "$elapsed" -ge "$startup_timeout" ]; then
		proto_notify_error "$config" TAP_TIMEOUT
		proto_kill_command "$config"
		proto_setup_failed "$config"
		return 1
	fi

	# edge owns the TAP and its initial address; do not clear or cycle the device.
	proto_init_update "$tunname" 1 1
	if [ "$mode" = static ]; then
		proto_add_ipv4_address "$ipaddr" "$prefix"
		[ -z "$gateway" ] || [ "$defaultroute" = 0 ] || proto_add_ipv4_route 0.0.0.0 0 "$gateway"
	fi
	proto_send_update "$config" || return 1

	if [ "$mode" = dhcp ]; then
		# netifd owns udhcpc separately; the edge command is never replaced.
		config_load firewall
		config_foreach n2n_find_zone zone
		json_init
		json_add_string name "${config}_4"
		json_add_string ifname "@$config"
		json_add_string proto dhcp
		proto_add_dynamic_defaults
		[ -z "$hostname" ] || json_add_string hostname "$hostname"
		[ -z "$zone" ] || json_add_string zone "$zone"
		[ -z "$ip4table" ] || json_add_string ip4table "$ip4table"
		json_add_array dns
		for endpoint in $dns; do json_add_string '' "$endpoint"; done
		json_close_array
		ubus call network add_dynamic "$(json_dump)" || {
			proto_notify_error "$config" DHCP_FAILED
			proto_kill_command "$config"
			proto_setup_failed "$config"
			return 1
		}
	fi
}

proto_n2n_teardown() {
	# netifd tears down DHCP aliases when the parent goes down.
	proto_kill_command "$1"
}

[ -n "$INCLUDE_ONLY" ] || add_protocol n2n
