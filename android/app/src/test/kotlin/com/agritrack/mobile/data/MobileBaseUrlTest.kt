package com.agritrack.mobile.data

import org.junit.Assert.assertEquals
import org.junit.Assert.assertThrows
import org.junit.Test

class MobileBaseUrlTest {
    @Test
    fun normalizesFarmLanAddresses() {
        assertEquals("http://192.168.1.25:5000", MobileBaseUrl.normalize("192.168.1.25:5000/"))
        assertEquals("http://10.0.2.2:5000", MobileBaseUrl.normalize(" http://10.0.2.2:5000/// "))
        assertEquals("https://agritrack.local", MobileBaseUrl.normalize("https://agritrack.local"))
    }

    @Test
    fun rejectsApiPathsAndUnsupportedSchemes() {
        assertThrows(IllegalArgumentException::class.java) {
            MobileBaseUrl.normalize("http://192.168.1.25:5000/api/mobile/v1")
        }
        assertThrows(IllegalArgumentException::class.java) {
            MobileBaseUrl.normalize("ftp://192.168.1.25")
        }
    }
}
