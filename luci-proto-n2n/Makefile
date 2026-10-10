# SPDX-License-Identifier: GPL-3.0-only
include $(TOPDIR)/rules.mk

LUCI_TITLE:=Support for N2N VPN interfaces
LUCI_DEPENDS:=+luci-base +n2n-edge +netifd +resolveip +ip

PKG_VERSION:=1.0.0
PKG_RELEASE:=2
PKG_LICENSE:=GPL-3.0-only

include $(TOPDIR)/feeds/luci/luci.mk

# call BuildPackage - OpenWrt buildroot signature
