package com.agritrack.mobile.data

import java.net.URI

internal object MobileBaseUrl {
    fun normalize(value: String?): String {
        val trimmed = value.orEmpty().trim().trimEnd('/')
        require(trimmed.isNotBlank()) { "Base URL is required" }

        val withScheme = if (trimmed.contains("://")) trimmed else "http://$trimmed"
        val uri = runCatching { URI(withScheme) }.getOrElse {
            throw IllegalArgumentException("Base URL is not valid")
        }
        val scheme = uri.scheme?.lowercase().orEmpty()
        require(scheme == "http" || scheme == "https") {
            "Base URL must start with http:// or https://"
        }
        require(!uri.host.isNullOrBlank()) {
            "Base URL must include a host or IP address"
        }
        require(uri.rawQuery == null && uri.rawFragment == null) {
            "Base URL must not include query text or a fragment"
        }
        require(uri.rawPath.isNullOrBlank() || uri.rawPath == "/") {
            "Use the server address only, without /api or another path"
        }

        return URI(scheme, uri.rawAuthority, null, null, null).toString().trimEnd('/')
    }
}
