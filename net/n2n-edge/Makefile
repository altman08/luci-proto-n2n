# SPDX-License-Identifier: GPL-3.0-only
#
# Copyright (C) 2020 - ntop.org and contributors
# Copyright (C) 2021-2022 ImmortalWrt.org

include $(TOPDIR)/rules.mk

PKG_NAME:=n2n-edge
PKG_VERSION:=3.0
PKG_RELEASE:=2

PKG_SOURCE:=n2n-$(PKG_VERSION).tar.gz
PKG_SOURCE_URL:=https://codeload.github.com/ntop/n2n/tar.gz/$(PKG_VERSION)?
PKG_HASH:=25fcabba7bfcf25f4c9cd7fecc7ce11de48beb0b0f3506053d8485604ea8f50d
# Keep a separate build directory from the full n2n source package.
PKG_BUILD_DIR:=$(BUILD_DIR)/$(PKG_NAME)-$(PKG_VERSION)
PKG_BUILD_PARALLEL:=1

# download.pl already retries; also bound stalled GitHub source transfers.
export CURL_OPTIONS+= --connect-timeout 10 --max-time 120 --retry 3 --retry-delay 2 --retry-max-time 420

PKG_LICENSE:=GPL-3.0-only
PKG_LICENSE_FILES:=LICENSE

include $(INCLUDE_DIR)/package.mk
include $(INCLUDE_DIR)/cmake.mk

# The upstream archive extracts into n2n-$(PKG_VERSION), not n2n-edge-*.
TAR_OPTIONS+= --transform='s,^n2n-$(PKG_VERSION),$(PKG_NAME)-$(PKG_VERSION),'

# Only build edge and its library; do not build supernode or the tools.
PKG_INSTALL:=

define Package/n2n-edge
  SECTION:=net
  CATEGORY:=Network
  SUBMENU:=VPN
  TITLE:=N2N peer-to-peer VPN (edge client only)
  URL:=https://github.com/ntop/n2n
  DEPENDS:=+libopenssl +libpthread +libzstd +libcap +kmod-tun
  CONFLICTS:=n2n
endef

define Package/n2n-edge/description
  N2N edge client binary only, without supernode, init scripts or UCI defaults.
  Install luci-proto-n2n to manage the client through netifd and LuCI.
endef

CMAKE_OPTIONS+= \
	-DN2N_OPTION_USE_PTHREAD=ON \
	-DN2N_OPTION_USE_OPENSSL=ON \
	-DN2N_OPTION_USE_PCAPLIB=ON \
	-DN2N_OPTION_USE_ZSTD=ON

define Build/Compile
	$(call Build/Compile/Default,edge)
endef

define Package/n2n-edge/install
	$(INSTALL_DIR) $(1)/usr/bin
	$(INSTALL_BIN) $(CMAKE_BINARY_DIR)/edge $(1)/usr/bin/n2n-edge
endef

$(eval $(call BuildPackage,n2n-edge))

# APK uses negative runtime dependencies instead of IPK's Conflicts field.
# Append after BuildPackage so !n2n is not treated as a build dependency.
ifneq ($(CONFIG_USE_APK),)
  Package/n2n-edge/DEPENDS += $(comma) !n2n
endif
