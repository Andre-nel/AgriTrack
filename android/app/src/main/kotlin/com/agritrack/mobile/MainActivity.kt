package com.agritrack.mobile

import android.os.Bundle
import android.os.Handler
import android.os.Looper
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import com.agritrack.mobile.data.FarmSnapshot
import com.agritrack.mobile.data.FarmSummary
import com.agritrack.mobile.data.LoginLoadResult
import com.agritrack.mobile.data.MobSummary
import com.agritrack.mobile.data.MobileApiClient
import com.agritrack.mobile.data.MobileRepository
import com.agritrack.mobile.data.OfflineCommandQueue
import com.agritrack.mobile.data.SecureTokenStore
import com.agritrack.mobile.data.SnapshotCache
import com.agritrack.mobile.data.SyncResult
import com.agritrack.mobile.data.SyncSummary
import java.time.LocalDate
import java.util.concurrent.ExecutorService
import java.util.concurrent.Executors

class MainActivity : ComponentActivity() {
    private val background: ExecutorService = Executors.newSingleThreadExecutor()
    private val main = Handler(Looper.getMainLooper())

    private lateinit var tokenStore: SecureTokenStore
    private lateinit var snapshotCache: SnapshotCache
    private lateinit var queue: OfflineCommandQueue

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        tokenStore = SecureTokenStore(this)
        snapshotCache = SnapshotCache(this)
        queue = OfflineCommandQueue(OfflineCommandQueue.SharedPreferencesCommandStore(this))

        val cachedSnapshot = snapshotCache.loadLast()
        val initialState = FieldUiState(
            selectedFarm = cachedSnapshot?.farm,
            snapshot = cachedSnapshot,
            selectedMobId = cachedSnapshot?.mobs?.firstOrNull()?.id.orEmpty(),
            pendingCount = queue.size(),
        )

        setContent {
            var uiState by remember { mutableStateOf(initialState) }

            fun <T> runTask(
                statusMessage: String,
                work: (MobileRepository) -> T,
                reduce: (FieldUiState, T) -> FieldUiState,
            ) {
                val baseUrl = uiState.baseUrl
                uiState = uiState.copy(isBusy = true, statusMessage = statusMessage)
                background.execute {
                    try {
                        val result = work(repository(baseUrl))
                        main.post {
                            uiState = reduce(uiState, result).copy(isBusy = false)
                        }
                    } catch (exc: Exception) {
                        main.post {
                            uiState = uiState.copy(
                                isBusy = false,
                                statusMessage = exc.message ?: "Operation failed",
                            )
                        }
                    }
                }
            }

            AgriTrackTheme {
                AgriTrackApp(
                    state = uiState,
                    onBaseUrlChange = { uiState = uiState.copy(baseUrl = it) },
                    onEmailChange = { uiState = uiState.copy(email = it) },
                    onPasswordChange = { uiState = uiState.copy(password = it) },
                    onRainfallDateChange = { uiState = uiState.copy(rainfallDate = it) },
                    onRainfallMmChange = { uiState = uiState.copy(rainfallMm = it) },
                    onRainfallNoteChange = { uiState = uiState.copy(rainfallNote = it) },
                    onMobSelected = { uiState = uiState.copy(selectedMobId = it) },
                    onMobNoteChange = { uiState = uiState.copy(mobNote = it) },
                    onMobTagsChange = { uiState = uiState.copy(mobTags = it) },
                    onLogin = {
                        val email = uiState.email
                        val password = uiState.password
                        runTask(
                            statusMessage = "Logging in...",
                            work = { repo ->
                                repo.loginAndLoad(
                                    email = email,
                                    password = password,
                                    deviceName = "Android Field Phone",
                                )
                            },
                            reduce = ::loginLoaded,
                        )
                    },
                    onQueueRainfall = {
                        uiState = queueRainfall(uiState)
                    },
                    onQueueMobNote = {
                        uiState = queueMobNote(uiState)
                    },
                    onSync = {
                        runTask(
                            statusMessage = "Syncing ${queue.size()} queued command(s)...",
                            work = { repo -> repo.syncQueuedCommands() },
                            reduce = ::synced,
                        )
                    },
                )
            }
        }
    }

    override fun onDestroy() {
        background.shutdown()
        super.onDestroy()
    }

    private fun repository(baseUrl: String): MobileRepository = MobileRepository(
        apiClient = MobileApiClient(baseUrl),
        tokenStore = tokenStore,
        snapshotCache = snapshotCache,
        queue = queue,
    )

    private fun loginLoaded(state: FieldUiState, result: LoginLoadResult): FieldUiState {
        val snapshot = result.snapshot
        if (snapshot == null) {
            return state.copy(
                selectedFarm = null,
                snapshot = null,
                selectedMobId = "",
                pendingCount = queue.size(),
                password = "",
                statusMessage = "Login worked, but this user has no farm access yet.",
            )
        }
        return state.copy(
            selectedFarm = snapshot.farm,
            snapshot = snapshot,
            selectedMobId = snapshot.mobs.firstOrNull()?.id.orEmpty(),
            pendingCount = queue.size(),
            password = "",
            statusMessage = "Loaded ${snapshot.farm.name}",
        )
    }

    private fun queueRainfall(state: FieldUiState): FieldUiState {
        val farm = state.selectedFarm
            ?: return state.copy(statusMessage = "Load a farm snapshot before queueing rainfall.")
        val recordedOn = runCatching { LocalDate.parse(state.rainfallDate).toString() }
            .getOrElse { return state.copy(statusMessage = "Date must use YYYY-MM-DD.") }
        val mm = state.rainfallMm.toDoubleOrNull()
            ?: return state.copy(statusMessage = "Rainfall must be a number.")
        if (mm < 0) {
            return state.copy(statusMessage = "Rainfall must be zero or more.")
        }
        return try {
            repository(state.baseUrl).queueRainfall(
                farmId = farm.id,
                recordedOn = recordedOn,
                mm = mm,
                note = state.rainfallNote,
            )
            state.copy(
                pendingCount = queue.size(),
                rainfallNote = "",
                statusMessage = "Queued rainfall. Pending: ${queue.size()}",
            )
        } catch (exc: Exception) {
            state.copy(statusMessage = exc.message ?: "Queue failed")
        }
    }

    private fun queueMobNote(state: FieldUiState): FieldUiState {
        val farm = state.selectedFarm
            ?: return state.copy(statusMessage = "Load a farm snapshot before queueing a mob note.")
        val mobId = state.selectedMobId.ifBlank {
            state.snapshot?.mobs?.firstOrNull()?.id.orEmpty()
        }
        if (mobId.isBlank()) {
            return state.copy(statusMessage = "No active mob is available in the loaded snapshot.")
        }
        if (state.mobNote.isBlank()) {
            return state.copy(statusMessage = "Mob note is required.")
        }
        val tags = state.mobTags
            .split(",")
            .map { it.trim() }
            .filter { it.isNotEmpty() }
        return try {
            repository(state.baseUrl).queueMobNote(
                farmId = farm.id,
                mobId = mobId,
                description = state.mobNote,
                tags = tags,
            )
            state.copy(
                selectedMobId = mobId,
                mobNote = "",
                pendingCount = queue.size(),
                statusMessage = "Queued mob note. Pending: ${queue.size()}",
            )
        } catch (exc: Exception) {
            state.copy(statusMessage = exc.message ?: "Queue failed")
        }
    }

    private fun synced(state: FieldUiState, summary: SyncSummary): FieldUiState = state.copy(
        pendingCount = summary.remainingQueueCount,
        lastSync = summary,
        statusMessage = "Sync applied ${summary.appliedCount}, failed ${summary.failedCount}. Pending: ${summary.remainingQueueCount}",
    )
}

private data class FieldUiState(
    val baseUrl: String = "http://10.0.2.2:5000",
    val email: String = "",
    val password: String = "",
    val isBusy: Boolean = false,
    val statusMessage: String = "Ready",
    val selectedFarm: FarmSummary? = null,
    val snapshot: FarmSnapshot? = null,
    val pendingCount: Int = 0,
    val lastSync: SyncSummary? = null,
    val rainfallDate: String = LocalDate.now().toString(),
    val rainfallMm: String = "",
    val rainfallNote: String = "",
    val selectedMobId: String = "",
    val mobNote: String = "",
    val mobTags: String = "field note",
)

@Composable
private fun AgriTrackTheme(content: @Composable () -> Unit) {
    MaterialTheme(
        colorScheme = lightColorScheme(
            primary = Color(0xFF315C37),
            onPrimary = Color.White,
            secondary = Color(0xFF3F667D),
            tertiary = Color(0xFF8A5B2E),
            background = Color(0xFFF7F8F4),
            surface = Color(0xFFFFFFFF),
            surfaceVariant = Color(0xFFE7EDE3),
            outline = Color(0xFFBAC5B4),
        ),
        content = content,
    )
}

@Composable
private fun AgriTrackApp(
    state: FieldUiState,
    onBaseUrlChange: (String) -> Unit,
    onEmailChange: (String) -> Unit,
    onPasswordChange: (String) -> Unit,
    onRainfallDateChange: (String) -> Unit,
    onRainfallMmChange: (String) -> Unit,
    onRainfallNoteChange: (String) -> Unit,
    onMobSelected: (String) -> Unit,
    onMobNoteChange: (String) -> Unit,
    onMobTagsChange: (String) -> Unit,
    onLogin: () -> Unit,
    onQueueRainfall: () -> Unit,
    onQueueMobNote: () -> Unit,
    onSync: () -> Unit,
) {
    Surface(
        modifier = Modifier.fillMaxSize(),
        color = MaterialTheme.colorScheme.background,
    ) {
        Column(
            modifier = Modifier
                .fillMaxSize()
                .verticalScroll(rememberScrollState())
                .padding(18.dp),
            verticalArrangement = Arrangement.spacedBy(18.dp),
        ) {
            Header(state)
            LoginScreen(
                state = state,
                onBaseUrlChange = onBaseUrlChange,
                onEmailChange = onEmailChange,
                onPasswordChange = onPasswordChange,
                onLogin = onLogin,
            )
            FarmHomeScreen(snapshot = state.snapshot)
            RainfallNoteScreen(
                state = state,
                onRainfallDateChange = onRainfallDateChange,
                onRainfallMmChange = onRainfallMmChange,
                onRainfallNoteChange = onRainfallNoteChange,
                onMobSelected = onMobSelected,
                onMobNoteChange = onMobNoteChange,
                onMobTagsChange = onMobTagsChange,
                onQueueRainfall = onQueueRainfall,
                onQueueMobNote = onQueueMobNote,
            )
            SyncStatusScreen(
                state = state,
                onSync = onSync,
            )
        }
    }
}

@Composable
private fun Header(state: FieldUiState) {
    Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
        Text(
            text = "AgriTrack Field Ops",
            style = MaterialTheme.typography.headlineMedium,
            color = MaterialTheme.colorScheme.primary,
            fontWeight = FontWeight.SemiBold,
        )
        StatusStrip(message = state.statusMessage, isBusy = state.isBusy)
    }
}

@Composable
private fun StatusStrip(message: String, isBusy: Boolean) {
    Surface(
        color = MaterialTheme.colorScheme.surfaceVariant,
        shape = RoundedCornerShape(8.dp),
        border = BorderStroke(1.dp, MaterialTheme.colorScheme.outline),
        modifier = Modifier.fillMaxWidth(),
    ) {
        Row(
            modifier = Modifier.padding(horizontal = 12.dp, vertical = 10.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            if (isBusy) {
                CircularProgressIndicator(
                    modifier = Modifier.size(18.dp),
                    strokeWidth = 2.dp,
                )
                Spacer(modifier = Modifier.width(10.dp))
            }
            Text(
                text = message,
                style = MaterialTheme.typography.bodyMedium,
                color = Color(0xFF233126),
                modifier = Modifier.weight(1f),
            )
        }
    }
}

@Composable
private fun LoginScreen(
    state: FieldUiState,
    onBaseUrlChange: (String) -> Unit,
    onEmailChange: (String) -> Unit,
    onPasswordChange: (String) -> Unit,
    onLogin: () -> Unit,
) {
    Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
        SectionTitle("Login")
        OutlinedTextField(
            value = state.baseUrl,
            onValueChange = onBaseUrlChange,
            label = { Text("Base URL") },
            singleLine = true,
            modifier = Modifier.fillMaxWidth(),
        )
        OutlinedTextField(
            value = state.email,
            onValueChange = onEmailChange,
            label = { Text("Email") },
            singleLine = true,
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Email),
            modifier = Modifier.fillMaxWidth(),
        )
        OutlinedTextField(
            value = state.password,
            onValueChange = onPasswordChange,
            label = { Text("Password") },
            singleLine = true,
            visualTransformation = PasswordVisualTransformation(),
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password),
            modifier = Modifier.fillMaxWidth(),
        )
        Button(
            onClick = onLogin,
            enabled = !state.isBusy && state.email.isNotBlank() && state.password.isNotBlank(),
            modifier = Modifier.fillMaxWidth(),
        ) {
            Text("Login")
        }
    }
}

@Composable
private fun FarmHomeScreen(snapshot: FarmSnapshot?) {
    Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
        SectionDivider()
        SectionTitle("Farm")
        if (snapshot == null) {
            Text(
                text = "No farm loaded",
                style = MaterialTheme.typography.bodyMedium,
                color = Color(0xFF5A6657),
            )
        } else {
            Text(
                text = snapshot.farm.name,
                style = MaterialTheme.typography.titleLarge,
                fontWeight = FontWeight.SemiBold,
                color = Color(0xFF233126),
            )
            Text(
                text = snapshot.farm.timezone,
                style = MaterialTheme.typography.bodySmall,
                color = Color(0xFF5A6657),
            )
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(10.dp),
            ) {
                MetricTile("Paddocks", snapshot.paddockCount.toString(), Modifier.weight(1f))
                MetricTile("Mobs", snapshot.mobCount.toString(), Modifier.weight(1f))
            }
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(10.dp),
            ) {
                MetricTile("Water", snapshot.waterAssetCount.toString(), Modifier.weight(1f))
                MetricTile("Notes", snapshot.mobEventCount.toString(), Modifier.weight(1f))
            }
        }
    }
}

@Composable
private fun RainfallNoteScreen(
    state: FieldUiState,
    onRainfallDateChange: (String) -> Unit,
    onRainfallMmChange: (String) -> Unit,
    onRainfallNoteChange: (String) -> Unit,
    onMobSelected: (String) -> Unit,
    onMobNoteChange: (String) -> Unit,
    onMobTagsChange: (String) -> Unit,
    onQueueRainfall: () -> Unit,
    onQueueMobNote: () -> Unit,
) {
    Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
        SectionDivider()
        SectionTitle("Rainfall")
        OutlinedTextField(
            value = state.rainfallDate,
            onValueChange = onRainfallDateChange,
            label = { Text("Date") },
            singleLine = true,
            modifier = Modifier.fillMaxWidth(),
        )
        OutlinedTextField(
            value = state.rainfallMm,
            onValueChange = onRainfallMmChange,
            label = { Text("Millimetres") },
            singleLine = true,
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Decimal),
            modifier = Modifier.fillMaxWidth(),
        )
        OutlinedTextField(
            value = state.rainfallNote,
            onValueChange = onRainfallNoteChange,
            label = { Text("Note") },
            minLines = 2,
            modifier = Modifier.fillMaxWidth(),
        )
        Button(
            onClick = onQueueRainfall,
            enabled = !state.isBusy && state.snapshot != null,
            modifier = Modifier.fillMaxWidth(),
        ) {
            Text("Queue Rainfall")
        }

        Spacer(modifier = Modifier.height(4.dp))
        SectionTitle("Mob Note")
        MobPicker(
            mobs = state.snapshot?.mobs.orEmpty(),
            selectedMobId = state.selectedMobId,
            onMobSelected = onMobSelected,
        )
        OutlinedTextField(
            value = state.mobTags,
            onValueChange = onMobTagsChange,
            label = { Text("Tags") },
            singleLine = true,
            modifier = Modifier.fillMaxWidth(),
        )
        OutlinedTextField(
            value = state.mobNote,
            onValueChange = onMobNoteChange,
            label = { Text("Note") },
            minLines = 3,
            modifier = Modifier.fillMaxWidth(),
        )
        Button(
            onClick = onQueueMobNote,
            enabled = !state.isBusy && state.snapshot?.mobs?.isNotEmpty() == true,
            modifier = Modifier.fillMaxWidth(),
        ) {
            Text("Queue Note")
        }
    }
}

@Composable
private fun MobPicker(
    mobs: List<MobSummary>,
    selectedMobId: String,
    onMobSelected: (String) -> Unit,
) {
    var expanded by remember { mutableStateOf(false) }
    val selectedMob = mobs.firstOrNull { it.id == selectedMobId } ?: mobs.firstOrNull()
    Box {
        OutlinedButton(
            onClick = { expanded = true },
            enabled = mobs.isNotEmpty(),
            modifier = Modifier.fillMaxWidth(),
        ) {
            Text(selectedMob?.name ?: "No mobs")
        }
        DropdownMenu(
            expanded = expanded,
            onDismissRequest = { expanded = false },
        ) {
            mobs.forEach { mob ->
                DropdownMenuItem(
                    text = { Text(mob.name) },
                    onClick = {
                        expanded = false
                        onMobSelected(mob.id)
                    },
                )
            }
        }
    }
}

@Composable
private fun SyncStatusScreen(
    state: FieldUiState,
    onSync: () -> Unit,
) {
    Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
        SectionDivider()
        SectionTitle("Sync")
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            MetricTile("Pending", state.pendingCount.toString(), Modifier.weight(1f))
            MetricTile("Applied", (state.lastSync?.appliedCount ?: 0).toString(), Modifier.weight(1f))
            MetricTile("Failed", (state.lastSync?.failedCount ?: 0).toString(), Modifier.weight(1f))
        }
        Button(
            onClick = onSync,
            enabled = !state.isBusy,
            modifier = Modifier.fillMaxWidth(),
        ) {
            Text("Sync Now")
        }
        state.lastSync?.results?.take(8)?.forEach { result ->
            SyncResultRow(result)
        }
    }
}

@Composable
private fun SyncResultRow(result: SyncResult) {
    Surface(
        color = if (result.status == "applied") Color(0xFFEAF2E6) else Color(0xFFF8EAE4),
        shape = RoundedCornerShape(8.dp),
        border = BorderStroke(1.dp, MaterialTheme.colorScheme.outline),
        modifier = Modifier.fillMaxWidth(),
    ) {
        Column(
            modifier = Modifier.padding(10.dp),
            verticalArrangement = Arrangement.spacedBy(2.dp),
        ) {
            Text(
                text = "${result.type} - ${result.status}",
                style = MaterialTheme.typography.bodyMedium,
                fontWeight = FontWeight.SemiBold,
            )
            if (result.duplicate) {
                Text(
                    text = "Duplicate replay",
                    style = MaterialTheme.typography.bodySmall,
                    color = Color(0xFF5A6657),
                )
            }
            result.errorMessage?.takeIf { it.isNotBlank() }?.let { message ->
                Text(
                    text = message,
                    style = MaterialTheme.typography.bodySmall,
                    color = Color(0xFF7A3424),
                )
            }
        }
    }
}

@Composable
private fun MetricTile(label: String, value: String, modifier: Modifier = Modifier) {
    Surface(
        modifier = modifier,
        color = MaterialTheme.colorScheme.surface,
        shape = RoundedCornerShape(8.dp),
        border = BorderStroke(1.dp, MaterialTheme.colorScheme.outline),
    ) {
        Column(
            modifier = Modifier.padding(12.dp),
            verticalArrangement = Arrangement.spacedBy(4.dp),
        ) {
            Text(
                text = label,
                style = MaterialTheme.typography.labelMedium,
                color = Color(0xFF5A6657),
            )
            Text(
                text = value,
                style = MaterialTheme.typography.titleLarge,
                fontWeight = FontWeight.SemiBold,
                color = Color(0xFF233126),
            )
        }
    }
}

@Composable
private fun SectionTitle(text: String) {
    Text(
        text = text,
        style = MaterialTheme.typography.titleMedium,
        fontWeight = FontWeight.SemiBold,
        color = Color(0xFF233126),
    )
}

@Composable
private fun SectionDivider() {
    HorizontalDivider(color = MaterialTheme.colorScheme.outline.copy(alpha = 0.7f))
}
