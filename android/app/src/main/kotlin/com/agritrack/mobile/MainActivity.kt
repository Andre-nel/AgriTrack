package com.agritrack.mobile

import android.content.Context
import android.net.ConnectivityManager
import android.net.Network
import android.net.NetworkCapabilities
import android.net.NetworkRequest
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.Checkbox
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
import androidx.compose.runtime.DisposableEffect
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
import com.agritrack.mobile.data.AnimalGroupTypeSummary
import com.agritrack.mobile.data.DecisionItemSummary
import com.agritrack.mobile.data.FarmSnapshot
import com.agritrack.mobile.data.FarmSummary
import com.agritrack.mobile.data.LocalFieldStore
import com.agritrack.mobile.data.LoginLoadResult
import com.agritrack.mobile.data.LogoutResult
import com.agritrack.mobile.data.MobSummary
import com.agritrack.mobile.data.MobileApiClient
import com.agritrack.mobile.data.MobileRepository
import com.agritrack.mobile.data.OutboxFailure
import com.agritrack.mobile.data.PaddockSummary
import com.agritrack.mobile.data.SecureTokenStore
import com.agritrack.mobile.data.SyncResult
import com.agritrack.mobile.data.SyncSummary
import com.agritrack.mobile.data.TaskSummary
import com.agritrack.mobile.data.WaterAssetSummary
import java.time.LocalDate
import java.util.concurrent.ExecutorService
import java.util.concurrent.Executors

class MainActivity : ComponentActivity() {
    private val background: ExecutorService = Executors.newSingleThreadExecutor()
    private val main = Handler(Looper.getMainLooper())
    private val autoSyncHandler = Handler(Looper.getMainLooper())

    private lateinit var tokenStore: SecureTokenStore
    private lateinit var fieldStore: LocalFieldStore
    private var connectivityCallback: ConnectivityManager.NetworkCallback? = null
    private var triggerConnectivitySync: (() -> Unit)? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        tokenStore = SecureTokenStore(this)
        fieldStore = LocalFieldStore(this)
        registerConnectivitySync()

        val hasStoredToken = runCatching { tokenStore.load() != null }.getOrElse {
            tokenStore.clear()
            false
        }
        val cachedSnapshot = if (hasStoredToken) fieldStore.loadLastSnapshot() else null
        val initialState = FieldUiState.fromSnapshot(
            snapshot = cachedSnapshot,
            pendingCount = fieldStore.pendingCount(),
            failedCommands = fieldStore.failedCommands(),
            syncIntervalMinutes = fieldStore.getSyncIntervalMinutes(),
        ).copy(
            isAuthenticated = hasStoredToken,
            statusMessage = if (hasStoredToken) "Ready" else "Log in to continue.",
        )

        setContent {
            var uiState by remember { mutableStateOf(initialState) }

            fun repository(baseUrl: String = uiState.baseUrl): MobileRepository = MobileRepository(
                apiClient = MobileApiClient(baseUrl),
                tokenStore = tokenStore,
                fieldStore = fieldStore,
            )

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
                            uiState = reduce(uiState, result)
                                .copy(isBusy = false, failedCommands = fieldStore.failedCommands())
                        }
                    } catch (exc: Exception) {
                        main.post {
                            uiState = uiState.copy(
                                isBusy = false,
                                pendingCount = fieldStore.pendingCount(),
                                failedCommands = fieldStore.failedCommands(),
                                statusMessage = exc.message ?: "Operation failed",
                            )
                        }
                    }
                }
            }

            fun syncNow(message: String, automatic: Boolean = false) {
                if (!uiState.isAuthenticated) {
                    return
                }
                if (uiState.isBusy) {
                    return
                }
                if (automatic && (uiState.pendingCount <= 0 || !isOnline())) {
                    return
                }
                runTask(
                    statusMessage = message,
                    work = { repo -> repo.syncQueuedCommands(refreshAfterSync = true) },
                    reduce = ::synced,
                )
            }

            fun queueAndMaybeSync(nextState: FieldUiState) {
                uiState = nextState.copy(
                    pendingCount = fieldStore.pendingCount(),
                    failedCommands = fieldStore.failedCommands(),
                )
                if (isOnline()) {
                    main.post { syncNow("Connected. Syncing queued field edits...", automatic = true) }
                }
            }

            triggerConnectivitySync = {
                if (uiState.isAuthenticated) {
                    syncNow("Connection restored. Syncing queued field edits...", automatic = true)
                }
            }

            DisposableEffect(uiState.syncIntervalMinutes, uiState.isAuthenticated) {
                if (!uiState.isAuthenticated) {
                    onDispose { }
                } else {
                    val runnable = object : Runnable {
                        override fun run() {
                            triggerConnectivitySync?.invoke()
                            autoSyncHandler.postDelayed(this, uiState.syncIntervalMinutes * 60_000L)
                        }
                    }
                    autoSyncHandler.postDelayed(runnable, uiState.syncIntervalMinutes * 60_000L)
                    onDispose { autoSyncHandler.removeCallbacks(runnable) }
                }
            }

            AgriTrackTheme {
                AgriTrackApp(
                    state = uiState,
                    onBaseUrlChange = { uiState = uiState.copy(baseUrl = it) },
                    onEmailChange = { uiState = uiState.copy(email = it) },
                    onPasswordChange = { uiState = uiState.copy(password = it) },
                    onOpenScreen = { uiState = uiState.copy(currentScreen = it) },
                    onBackHome = { uiState = uiState.copy(currentScreen = AppScreen.Home) },
                    onSyncIntervalChange = { uiState = uiState.copy(syncIntervalText = it) },
                    onSaveSyncInterval = {
                        val interval = uiState.syncIntervalText.toIntOrNull()?.coerceAtLeast(1) ?: 5
                        repository().setSyncIntervalMinutes(interval)
                        uiState = uiState.copy(
                            syncIntervalMinutes = interval,
                            syncIntervalText = interval.toString(),
                            statusMessage = "Auto-sync interval set to $interval minute(s).",
                        )
                    },
                    onLogin = {
                        val email = uiState.email
                        val password = uiState.password
                        runTask(
                            statusMessage = "Logging in...",
                            work = { repo -> repo.loginAndLoad(email, password, "Android Field Phone") },
                            reduce = ::loginLoaded,
                        )
                    },
                    onLogout = {
                        runTask(
                            statusMessage = "Logging out...",
                            work = { repo -> repo.logout() },
                            reduce = ::loggedOut,
                        )
                    },
                    onRefresh = {
                        runTask(
                            statusMessage = "Refreshing current farm snapshot...",
                            work = { repo -> repo.refreshSnapshot(uiState.selectedFarm?.id) },
                            reduce = ::refreshed,
                        )
                    },
                    onRainfallDateChange = { uiState = uiState.copy(rainfallDate = it) },
                    onRainfallMmChange = { uiState = uiState.copy(rainfallMm = it) },
                    onRainfallNoteChange = { uiState = uiState.copy(rainfallNote = it) },
                    onMobSelected = { uiState = selectMob(uiState, it) },
                    onPaddockSelected = { uiState = selectPaddock(uiState, it) },
                    onWaterAssetSelected = { uiState = selectWaterAsset(uiState, it) },
                    onAnimalGroupSelected = { uiState = selectAnimalGroup(uiState, it) },
                    onMoveNoteChange = { uiState = uiState.copy(moveNote = it) },
                    onStockQuantityChange = { uiState = uiState.copy(stockQuantity = it) },
                    onStockNoteChange = { uiState = uiState.copy(stockNote = it) },
                    onTransferDestinationChange = { uiState = uiState.copy(transferDestinationMobId = it) },
                    onTransferQuantityChange = { uiState = uiState.copy(transferQuantity = it) },
                    onTransferNoteChange = { uiState = uiState.copy(transferNote = it) },
                    onPaddockStatusChange = { uiState = uiState.copy(paddockStatus = it) },
                    onPaddockNotesChange = { uiState = uiState.copy(paddockNotes = it) },
                    onPaddockTagsChange = { uiState = uiState.copy(paddockTags = it) },
                    onWaterStatusChange = { uiState = uiState.copy(waterStatus = it) },
                    onWaterLevelChange = { uiState = uiState.copy(waterLevel = it) },
                    onWaterActiveChange = { uiState = uiState.copy(waterActive = it) },
                    onTaskHeadingChange = { uiState = uiState.copy(taskHeading = it) },
                    onTaskDescriptionChange = { uiState = uiState.copy(taskDescription = it) },
                    onTaskDueDateChange = { uiState = uiState.copy(taskDueDate = it) },
                    onTaskCommentChange = { uiState = uiState.copy(taskComment = it) },
                    onStartTaskForEntity = { entityType, entityId, heading ->
                        uiState = uiState.copy(
                            currentScreen = AppScreen.TaskCreate,
                            taskEntityType = entityType,
                            taskEntityId = entityId,
                            taskHeading = heading,
                            taskDescription = "",
                            taskDueDate = LocalDate.now().toString(),
                        )
                    },
                    onQueueRainfall = { queueAndMaybeSync(queueRainfall(uiState, repository())) },
                    onQueueMobMove = { queueAndMaybeSync(queueMobMove(uiState, repository())) },
                    onQueueStockCount = { queueAndMaybeSync(queueStockCount(uiState, repository())) },
                    onQueueMobTransfer = { queueAndMaybeSync(queueMobTransfer(uiState, repository())) },
                    onQueuePaddockUpdate = { queueAndMaybeSync(queuePaddockUpdate(uiState, repository())) },
                    onQueueWaterUpdate = { queueAndMaybeSync(queueWaterUpdate(uiState, repository())) },
                    onQueueTaskCreate = { queueAndMaybeSync(queueTaskCreate(uiState, repository())) },
                    onQueueTaskStatus = { task, status ->
                        queueAndMaybeSync(queueTaskStatus(uiState, repository(), task, status))
                    },
                    onQueueTaskComment = { task ->
                        queueAndMaybeSync(queueTaskComment(uiState, repository(), task))
                    },
                    onSync = { syncNow("Syncing ${fieldStore.pendingCount()} queued command(s)...") },
                )
            }
        }
    }

    override fun onDestroy() {
        triggerConnectivitySync = null
        connectivityCallback?.let {
            (getSystemService(Context.CONNECTIVITY_SERVICE) as ConnectivityManager)
                .unregisterNetworkCallback(it)
        }
        autoSyncHandler.removeCallbacksAndMessages(null)
        background.shutdown()
        super.onDestroy()
    }

    private fun registerConnectivitySync() {
        val manager = getSystemService(Context.CONNECTIVITY_SERVICE) as ConnectivityManager
        val request = NetworkRequest.Builder()
            .addCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET)
            .build()
        connectivityCallback = object : ConnectivityManager.NetworkCallback() {
            override fun onAvailable(network: Network) {
                main.post { triggerConnectivitySync?.invoke() }
            }
        }
        manager.registerNetworkCallback(request, connectivityCallback!!)
    }

    private fun isOnline(): Boolean {
        val manager = getSystemService(Context.CONNECTIVITY_SERVICE) as ConnectivityManager
        val network = manager.activeNetwork ?: return false
        val capabilities = manager.getNetworkCapabilities(network) ?: return false
        return capabilities.hasCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET)
    }
}

private data class FieldUiState(
    val currentScreen: AppScreen = AppScreen.Home,
    val baseUrl: String = "http://10.0.2.2:5000",
    val email: String = "",
    val password: String = "",
    val isAuthenticated: Boolean = false,
    val isBusy: Boolean = false,
    val statusMessage: String = "Ready",
    val selectedFarm: FarmSummary? = null,
    val snapshot: FarmSnapshot? = null,
    val pendingCount: Int = 0,
    val failedCommands: List<OutboxFailure> = emptyList(),
    val lastSync: SyncSummary? = null,
    val animalGroupTypes: List<AnimalGroupTypeSummary> = emptyList(),
    val syncIntervalMinutes: Int = 5,
    val syncIntervalText: String = "5",
    val rainfallDate: String = LocalDate.now().toString(),
    val rainfallMm: String = "",
    val rainfallNote: String = "",
    val selectedMobId: String = "",
    val selectedPaddockId: String = "",
    val selectedWaterAssetId: String = "",
    val selectedAnimalGroupTypeId: String = "",
    val moveNote: String = "",
    val stockQuantity: String = "",
    val stockNote: String = "",
    val transferDestinationMobId: String = "",
    val transferQuantity: String = "",
    val transferNote: String = "",
    val paddockStatus: String = "",
    val paddockNotes: String = "",
    val paddockTags: String = "",
    val waterStatus: String = "",
    val waterLevel: String = "",
    val waterActive: Boolean = true,
    val taskEntityType: String = "farm",
    val taskEntityId: String = "",
    val taskHeading: String = "",
    val taskDescription: String = "",
    val taskDueDate: String = LocalDate.now().toString(),
    val taskComment: String = "",
) {
    companion object {
        fun fromSnapshot(
            snapshot: FarmSnapshot?,
            pendingCount: Int,
            failedCommands: List<OutboxFailure>,
            syncIntervalMinutes: Int,
        ): FieldUiState {
            val firstMob = snapshot?.mobs?.firstOrNull()
            val firstPaddock = snapshot?.paddocks?.firstOrNull()
            val firstWater = snapshot?.waterAssets?.firstOrNull()
            val firstBalance = firstMob?.balances?.firstOrNull()
            return FieldUiState(
                selectedFarm = snapshot?.farm,
                snapshot = snapshot,
                pendingCount = pendingCount,
                failedCommands = failedCommands,
                animalGroupTypes = animalGroupTypesFromSnapshot(snapshot),
                syncIntervalMinutes = syncIntervalMinutes,
                syncIntervalText = syncIntervalMinutes.toString(),
                selectedMobId = firstMob?.id.orEmpty(),
                selectedPaddockId = firstPaddock?.id.orEmpty(),
                selectedWaterAssetId = firstWater?.id.orEmpty(),
                selectedAnimalGroupTypeId = firstBalance?.animalGroupTypeId.orEmpty(),
                stockQuantity = firstBalance?.headCount?.toString().orEmpty(),
                transferDestinationMobId = snapshot?.mobs?.drop(1)?.firstOrNull()?.id.orEmpty(),
                paddockStatus = firstPaddock?.status.orEmpty(),
                paddockNotes = firstPaddock?.notes.orEmpty(),
                paddockTags = firstPaddock?.tagLabel.orEmpty(),
                waterStatus = firstWater?.status.orEmpty(),
                waterLevel = firstWater?.waterLevel.orEmpty(),
                waterActive = firstWater?.active ?: true,
            )
        }
    }
}

private enum class AppScreen {
    Home,
    Farm,
    FarmMap,
    Calendar,
    Mobs,
    Paddocks,
    WaterAssets,
    Rainfall,
    MoveMob,
    StockCount,
    TransferMob,
    PaddockEdit,
    WaterEdit,
    TaskCreate,
    Sync,
}

@Composable
private fun AgriTrackTheme(content: @Composable () -> Unit) {
    MaterialTheme(
        colorScheme = lightColorScheme(
            primary = Color(0xFF315C37),
            onPrimary = Color.White,
            secondary = Color(0xFF3F667D),
            tertiary = Color(0xFF8A5B2E),
            background = Color(0xFFF7F8F4),
            surface = Color.White,
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
    onOpenScreen: (AppScreen) -> Unit,
    onBackHome: () -> Unit,
    onSyncIntervalChange: (String) -> Unit,
    onSaveSyncInterval: () -> Unit,
    onLogin: () -> Unit,
    onLogout: () -> Unit,
    onRefresh: () -> Unit,
    onRainfallDateChange: (String) -> Unit,
    onRainfallMmChange: (String) -> Unit,
    onRainfallNoteChange: (String) -> Unit,
    onMobSelected: (String) -> Unit,
    onPaddockSelected: (String) -> Unit,
    onWaterAssetSelected: (String) -> Unit,
    onAnimalGroupSelected: (String) -> Unit,
    onMoveNoteChange: (String) -> Unit,
    onStockQuantityChange: (String) -> Unit,
    onStockNoteChange: (String) -> Unit,
    onTransferDestinationChange: (String) -> Unit,
    onTransferQuantityChange: (String) -> Unit,
    onTransferNoteChange: (String) -> Unit,
    onPaddockStatusChange: (String) -> Unit,
    onPaddockNotesChange: (String) -> Unit,
    onPaddockTagsChange: (String) -> Unit,
    onWaterStatusChange: (String) -> Unit,
    onWaterLevelChange: (String) -> Unit,
    onWaterActiveChange: (Boolean) -> Unit,
    onTaskHeadingChange: (String) -> Unit,
    onTaskDescriptionChange: (String) -> Unit,
    onTaskDueDateChange: (String) -> Unit,
    onTaskCommentChange: (String) -> Unit,
    onStartTaskForEntity: (String, String, String) -> Unit,
    onQueueRainfall: () -> Unit,
    onQueueMobMove: () -> Unit,
    onQueueStockCount: () -> Unit,
    onQueueMobTransfer: () -> Unit,
    onQueuePaddockUpdate: () -> Unit,
    onQueueWaterUpdate: () -> Unit,
    onQueueTaskCreate: () -> Unit,
    onQueueTaskStatus: (TaskSummary, String) -> Unit,
    onQueueTaskComment: (TaskSummary) -> Unit,
    onSync: () -> Unit,
) {
    Surface(modifier = Modifier.fillMaxSize(), color = MaterialTheme.colorScheme.background) {
        Column(
            modifier = Modifier
                .fillMaxSize()
                .verticalScroll(rememberScrollState())
                .padding(18.dp),
            verticalArrangement = Arrangement.spacedBy(16.dp),
        ) {
            Header(state, onLogout)
            if (!state.isAuthenticated) {
                LoginScreen(
                    state,
                    onBaseUrlChange,
                    onEmailChange,
                    onPasswordChange,
                    onLogin,
                )
            } else when (state.currentScreen) {
                AppScreen.Home -> HomeScreen(
                    state,
                    onBaseUrlChange,
                    onEmailChange,
                    onPasswordChange,
                    onLogin,
                    onOpenScreen,
                    onRefresh,
                    onSync,
                )
                AppScreen.Farm -> FarmScreen(state, onBackHome)
                AppScreen.FarmMap -> FarmMapScreen(state, onBackHome)
                AppScreen.Calendar -> CalendarScreen(
                    state,
                    onBackHome,
                    onQueueTaskStatus,
                    onTaskCommentChange,
                    onQueueTaskComment,
                )
                AppScreen.Mobs -> MobsScreen(
                    state,
                    onBackHome,
                    onMobSelected,
                    onOpenScreen,
                    onStartTaskForEntity,
                )
                AppScreen.Paddocks -> PaddocksScreen(
                    state,
                    onBackHome,
                    onPaddockSelected,
                    onOpenScreen,
                    onStartTaskForEntity,
                )
                AppScreen.WaterAssets -> WaterAssetsScreen(
                    state,
                    onBackHome,
                    onWaterAssetSelected,
                    onOpenScreen,
                    onStartTaskForEntity,
                )
                AppScreen.Rainfall -> RainfallScreen(
                    state,
                    onBackHome,
                    onRainfallDateChange,
                    onRainfallMmChange,
                    onRainfallNoteChange,
                    onQueueRainfall,
                )
                AppScreen.MoveMob -> MoveMobScreen(
                    state,
                    onBackHome,
                    onMobSelected,
                    onPaddockSelected,
                    onMoveNoteChange,
                    onQueueMobMove,
                )
                AppScreen.StockCount -> StockCountScreen(
                    state,
                    onBackHome,
                    onMobSelected,
                    onAnimalGroupSelected,
                    onStockQuantityChange,
                    onStockNoteChange,
                    onQueueStockCount,
                )
                AppScreen.TransferMob -> TransferMobScreen(
                    state,
                    onBackHome,
                    onMobSelected,
                    onTransferDestinationChange,
                    onAnimalGroupSelected,
                    onTransferQuantityChange,
                    onTransferNoteChange,
                    onQueueMobTransfer,
                )
                AppScreen.PaddockEdit -> PaddockEditScreen(
                    state,
                    onBackHome,
                    onPaddockStatusChange,
                    onPaddockNotesChange,
                    onPaddockTagsChange,
                    onQueuePaddockUpdate,
                )
                AppScreen.WaterEdit -> WaterEditScreen(
                    state,
                    onBackHome,
                    onWaterStatusChange,
                    onWaterLevelChange,
                    onWaterActiveChange,
                    onQueueWaterUpdate,
                )
                AppScreen.TaskCreate -> TaskCreateScreen(
                    state,
                    onBackHome,
                    onTaskHeadingChange,
                    onTaskDescriptionChange,
                    onTaskDueDateChange,
                    onQueueTaskCreate,
                )
                AppScreen.Sync -> SyncScreen(
                    state,
                    onBackHome,
                    onSyncIntervalChange,
                    onSaveSyncInterval,
                    onSync,
                )
            }
        }
    }
}

@Composable
private fun Header(state: FieldUiState, onLogout: () -> Unit) {
    Surface(
        color = MaterialTheme.colorScheme.surface,
        shape = RoundedCornerShape(8.dp),
        border = BorderStroke(1.dp, MaterialTheme.colorScheme.outline),
        modifier = Modifier.fillMaxWidth(),
    ) {
        Row(
            modifier = Modifier.padding(12.dp),
            horizontalArrangement = Arrangement.spacedBy(12.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            if (state.isBusy) {
                CircularProgressIndicator(modifier = Modifier.padding(4.dp))
            }
            Column(modifier = Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(2.dp)) {
                Text("AgriTrack Field", style = MaterialTheme.typography.titleLarge, fontWeight = FontWeight.Bold)
                Text(state.statusMessage, style = MaterialTheme.typography.bodyMedium, color = Color(0xFF516052))
            }
            if (state.isAuthenticated) {
                OutlinedButton(onClick = onLogout, enabled = !state.isBusy) {
                    Text("Log out")
                }
            }
        }
    }
}

@Composable
private fun HomeScreen(
    state: FieldUiState,
    onBaseUrlChange: (String) -> Unit,
    onEmailChange: (String) -> Unit,
    onPasswordChange: (String) -> Unit,
    onLogin: () -> Unit,
    onOpenScreen: (AppScreen) -> Unit,
    onRefresh: () -> Unit,
    onSync: () -> Unit,
) {
    Column(verticalArrangement = Arrangement.spacedBy(16.dp)) {
        LoginScreen(state, onBaseUrlChange, onEmailChange, onPasswordChange, onLogin)
        if (state.isAuthenticated) {
            FarmSummaryPanel(state, onRefresh)
            DecisionFeedPanel(state.snapshot?.decisionFeed.orEmpty())
            DashboardMenu(state, onOpenScreen)
            SyncMiniPanel(state, onOpenScreen, onSync)
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
    SectionCard("Login") {
        if (state.isAuthenticated) {
            Text(state.selectedFarm?.name ?: "Signed in", fontWeight = FontWeight.SemiBold)
            Text(state.baseUrl, style = MaterialTheme.typography.bodySmall, color = Color(0xFF516052))
        } else {
            OutlinedTextField(state.baseUrl, onBaseUrlChange, label = { Text("Base URL") }, modifier = Modifier.fillMaxWidth())
            OutlinedTextField(
                state.email,
                onEmailChange,
                label = { Text("Email") },
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Email),
                singleLine = true,
                modifier = Modifier.fillMaxWidth(),
            )
            OutlinedTextField(
                state.password,
                onPasswordChange,
                label = { Text("Password") },
                visualTransformation = PasswordVisualTransformation(),
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password),
                singleLine = true,
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
}

@Composable
private fun FarmSummaryPanel(state: FieldUiState, onRefresh: () -> Unit) {
    SectionCard("Current Farm") {
        val snapshot = state.snapshot
        if (snapshot == null) {
            Text("No farm loaded", color = Color(0xFF516052))
            return@SectionCard
        }
        Text(snapshot.farm.name, style = MaterialTheme.typography.titleLarge, fontWeight = FontWeight.Bold)
        Text(snapshot.farm.timezone, style = MaterialTheme.typography.bodySmall, color = Color(0xFF516052))
        MetricRows(
            listOf(
                "Paddocks" to snapshot.paddockCount.toString(),
                "Mobs" to snapshot.mobCount.toString(),
                "Water" to snapshot.waterAssetCount.toString(),
                "Tasks" to snapshot.taskCount.toString(),
                "Calendar" to snapshot.calendarItemCount.toString(),
                "Decisions" to snapshot.decisionCount.toString(),
            )
        )
        Button(onClick = onRefresh, enabled = !state.isBusy, modifier = Modifier.fillMaxWidth()) {
            Text("Refresh Snapshot")
        }
    }
}

@Composable
private fun DashboardMenu(state: FieldUiState, onOpenScreen: (AppScreen) -> Unit) {
    SectionCard("Dashboard") {
        DashboardButton("All Farms Map", "View cached farm and water map features", state.snapshot != null) {
            onOpenScreen(AppScreen.FarmMap)
        }
        DashboardButton("Calendar", "Tasks and activities for the coming days", state.snapshot != null) {
            onOpenScreen(AppScreen.Calendar)
        }
        DashboardButton("Farm", "Farm counts, current state, and quick metrics", state.snapshot != null) {
            onOpenScreen(AppScreen.Farm)
        }
        DashboardButton("Mobs", "Counts, moves, transfers, and linked tasks", state.snapshot?.mobs?.isNotEmpty() == true) {
            onOpenScreen(AppScreen.Mobs)
        }
        DashboardButton("Paddocks", "Expected stock, water links, status, notes, and tags", state.snapshot?.paddocks?.isNotEmpty() == true) {
            onOpenScreen(AppScreen.Paddocks)
        }
        DashboardButton("Water Assets", "Update status and water levels", state.snapshot?.waterAssets?.isNotEmpty() == true) {
            onOpenScreen(AppScreen.WaterAssets)
        }
        DashboardButton("Rainfall", "View recent rain and record a reading", state.snapshot != null) {
            onOpenScreen(AppScreen.Rainfall)
        }
    }
}

@Composable
private fun DashboardButton(title: String, detail: String, enabled: Boolean, onClick: () -> Unit) {
    OutlinedButton(onClick = onClick, enabled = enabled, modifier = Modifier.fillMaxWidth()) {
        Column(modifier = Modifier.fillMaxWidth().padding(vertical = 6.dp), verticalArrangement = Arrangement.spacedBy(2.dp)) {
            Text(title, fontWeight = FontWeight.SemiBold)
            Text(detail, style = MaterialTheme.typography.bodySmall, color = Color(0xFF516052))
        }
    }
}

@Composable
private fun DecisionFeedPanel(items: List<DecisionItemSummary>) {
    SectionCard("Decision Feed") {
        if (items.isEmpty()) {
            Text("No urgent field decisions in the cached snapshot.", color = Color(0xFF516052))
        }
        items.take(6).forEach { item ->
            Surface(
                color = if (item.severity == "high") Color(0xFFF8EAE4) else Color(0xFFFFF6DF),
                shape = RoundedCornerShape(8.dp),
                border = BorderStroke(1.dp, MaterialTheme.colorScheme.outline),
                modifier = Modifier.fillMaxWidth(),
            ) {
                Column(modifier = Modifier.padding(10.dp), verticalArrangement = Arrangement.spacedBy(3.dp)) {
                    Text(item.title, fontWeight = FontWeight.SemiBold)
                    Text(item.detail, style = MaterialTheme.typography.bodySmall, color = Color(0xFF516052))
                }
            }
        }
    }
}

@Composable
private fun SyncMiniPanel(state: FieldUiState, onOpenScreen: (AppScreen) -> Unit, onSync: () -> Unit) {
    SectionCard("Sync") {
        MetricRows(
            listOf(
                "Pending" to state.pendingCount.toString(),
                "Applied" to (state.lastSync?.appliedCount ?: 0).toString(),
                "Failed" to state.failedCommands.size.toString(),
            )
        )
        Row(horizontalArrangement = Arrangement.spacedBy(10.dp), modifier = Modifier.fillMaxWidth()) {
            Button(onClick = onSync, enabled = !state.isBusy, modifier = Modifier.weight(1f)) {
                Text("Sync Now")
            }
            OutlinedButton(onClick = { onOpenScreen(AppScreen.Sync) }, modifier = Modifier.weight(1f)) {
                Text("Sync Details")
            }
        }
    }
}

@Composable
private fun FarmScreen(state: FieldUiState, onBackHome: () -> Unit) {
    FormScaffold("Farm", onBackHome) {
        val snapshot = state.snapshot ?: return@FormScaffold
        MetricRows(
            listOf(
                "Paddocks" to snapshot.paddockCount.toString(),
                "Mobs" to snapshot.mobCount.toString(),
                "Water assets" to snapshot.waterAssetCount.toString(),
                "Open tasks" to snapshot.taskCount.toString(),
                "Map features" to snapshot.mapFeatures.size.toString(),
                "Recent rain" to snapshot.rainfallCount.toString(),
            )
        )
    }
}

@Composable
private fun FarmMapScreen(state: FieldUiState, onBackHome: () -> Unit) {
    FormScaffold("Farm Map", onBackHome) {
        val snapshot = state.snapshot ?: return@FormScaffold
        snapshot.mapWarnings.forEach { warning -> Text(warning, color = Color(0xFF7A3424)) }
        snapshot.mapFeatures.take(80).forEach { feature ->
            EntityCard(
                title = feature.name,
                detail = listOf(
                    feature.featureType,
                    feature.waterAlertLevel,
                    feature.waterAlertMessage,
                ).filterNotNull().filter { it.isNotBlank() }.joinToString(" | "),
            )
        }
    }
}

@Composable
private fun CalendarScreen(
    state: FieldUiState,
    onBackHome: () -> Unit,
    onQueueTaskStatus: (TaskSummary, String) -> Unit,
    onTaskCommentChange: (String) -> Unit,
    onQueueTaskComment: (TaskSummary) -> Unit,
) {
    FormScaffold("Calendar", onBackHome) {
        val snapshot = state.snapshot ?: return@FormScaffold
        SectionTitle("Upcoming")
        snapshot.calendarItems.take(30).forEach { item ->
            EntityCard(item.title, "${item.date} | ${item.badgeText ?: item.kind}")
        }
        SectionTitle("Tasks")
        snapshot.tasks.forEach { task ->
            Surface(
                color = MaterialTheme.colorScheme.surface,
                shape = RoundedCornerShape(8.dp),
                border = BorderStroke(1.dp, MaterialTheme.colorScheme.outline),
                modifier = Modifier.fillMaxWidth(),
            ) {
                Column(modifier = Modifier.padding(10.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    Text("${task.displayKey} ${task.heading}", fontWeight = FontWeight.SemiBold)
                    Text("${task.statusLabel} | ${task.dueDate ?: "no due date"}", color = Color(0xFF516052))
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp), modifier = Modifier.fillMaxWidth()) {
                        OutlinedButton(onClick = { onQueueTaskStatus(task, "in_progress") }, modifier = Modifier.weight(1f)) {
                            Text("Start")
                        }
                        OutlinedButton(onClick = { onQueueTaskStatus(task, "closed") }, modifier = Modifier.weight(1f)) {
                            Text("Close")
                        }
                    }
                    OutlinedTextField(
                        value = state.taskComment,
                        onValueChange = onTaskCommentChange,
                        label = { Text("Task note") },
                        modifier = Modifier.fillMaxWidth(),
                    )
                    Button(
                        onClick = { onQueueTaskComment(task) },
                        enabled = state.taskComment.isNotBlank(),
                        modifier = Modifier.fillMaxWidth(),
                    ) {
                        Text("Add Note")
                    }
                }
            }
        }
    }
}

@Composable
private fun MobsScreen(
    state: FieldUiState,
    onBackHome: () -> Unit,
    onMobSelected: (String) -> Unit,
    onOpenScreen: (AppScreen) -> Unit,
    onStartTaskForEntity: (String, String, String) -> Unit,
) {
    FormScaffold("Mobs", onBackHome) {
        state.snapshot?.mobs.orEmpty().forEach { mob ->
            EntityCard("${mob.name} (${mob.totalHead})", mob.balances.joinToString(", ") { "${it.headCount} ${it.animalGroupType.label}" })
            RelatedTasks(state.snapshot?.tasks.orEmpty(), "mob", mob.id)
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp), modifier = Modifier.fillMaxWidth()) {
                OutlinedButton(onClick = { onMobSelected(mob.id); onOpenScreen(AppScreen.MoveMob) }, modifier = Modifier.weight(1f)) {
                    Text("Move")
                }
                OutlinedButton(onClick = { onMobSelected(mob.id); onOpenScreen(AppScreen.StockCount) }, modifier = Modifier.weight(1f)) {
                    Text("Count")
                }
            }
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp), modifier = Modifier.fillMaxWidth()) {
                OutlinedButton(onClick = { onMobSelected(mob.id); onOpenScreen(AppScreen.TransferMob) }, modifier = Modifier.weight(1f)) {
                    Text("Transfer")
                }
                OutlinedButton(
                    onClick = { onStartTaskForEntity("mob", mob.id, "Check ${mob.name}") },
                    modifier = Modifier.weight(1f),
                ) {
                    Text("Task")
                }
            }
            SectionDivider()
        }
    }
}

@Composable
private fun PaddocksScreen(
    state: FieldUiState,
    onBackHome: () -> Unit,
    onPaddockSelected: (String) -> Unit,
    onOpenScreen: (AppScreen) -> Unit,
    onStartTaskForEntity: (String, String, String) -> Unit,
) {
    FormScaffold("Paddocks", onBackHome) {
        val snapshot = state.snapshot ?: return@FormScaffold
        snapshot.paddocks.forEach { paddock ->
            val grazing = snapshot.grazingByPaddock.firstOrNull { it.paddockId == paddock.id }
            val water = snapshot.waterAssets.filter { it.locationPaddockId == paddock.id || paddock.id in it.servedPaddockIds }
            EntityCard(
                title = "${paddock.name} | ${paddock.status}",
                detail = "Expected stock: ${grazing?.totalHead ?: 0.0}; Water links: ${water.size}; Tags: ${paddock.tagLabel.ifBlank { "none" }}",
            )
            grazing?.mobs.orEmpty().forEach { mob -> Text("${mob.mobName}: ${mob.allocationPct}%") }
            RelatedTasks(snapshot.tasks, "paddock", paddock.id)
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp), modifier = Modifier.fillMaxWidth()) {
                OutlinedButton(onClick = { onPaddockSelected(paddock.id); onOpenScreen(AppScreen.PaddockEdit) }, modifier = Modifier.weight(1f)) {
                    Text("Edit")
                }
                OutlinedButton(
                    onClick = { onStartTaskForEntity("paddock", paddock.id, "Inspect ${paddock.name}") },
                    modifier = Modifier.weight(1f),
                ) {
                    Text("Task")
                }
            }
            SectionDivider()
        }
    }
}

@Composable
private fun WaterAssetsScreen(
    state: FieldUiState,
    onBackHome: () -> Unit,
    onWaterAssetSelected: (String) -> Unit,
    onOpenScreen: (AppScreen) -> Unit,
    onStartTaskForEntity: (String, String, String) -> Unit,
) {
    FormScaffold("Water Assets", onBackHome) {
        val snapshot = state.snapshot ?: return@FormScaffold
        snapshot.waterAssets.forEach { asset ->
            EntityCard(
                title = asset.name,
                detail = "${asset.assetTypeLabel} | ${asset.status ?: "unknown"} | level ${asset.waterLevel ?: "unknown"}",
            )
            asset.networkWarning?.let { Text(it, color = Color(0xFF7A3424)) }
            RelatedTasks(snapshot.tasks, "water_asset", asset.id)
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp), modifier = Modifier.fillMaxWidth()) {
                OutlinedButton(onClick = { onWaterAssetSelected(asset.id); onOpenScreen(AppScreen.WaterEdit) }, modifier = Modifier.weight(1f)) {
                    Text("Update")
                }
                OutlinedButton(
                    onClick = { onStartTaskForEntity("water_asset", asset.id, "Check ${asset.name}") },
                    modifier = Modifier.weight(1f),
                ) {
                    Text("Task")
                }
            }
            SectionDivider()
        }
    }
}

@Composable
private fun RainfallScreen(
    state: FieldUiState,
    onBackHome: () -> Unit,
    onRainfallDateChange: (String) -> Unit,
    onRainfallMmChange: (String) -> Unit,
    onRainfallNoteChange: (String) -> Unit,
    onQueueRainfall: () -> Unit,
) {
    FormScaffold("Rainfall", onBackHome) {
        OutlinedTextField(state.rainfallDate, onRainfallDateChange, label = { Text("Date") }, modifier = Modifier.fillMaxWidth())
        OutlinedTextField(
            state.rainfallMm,
            onRainfallMmChange,
            label = { Text("Millimetres") },
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Decimal),
            modifier = Modifier.fillMaxWidth(),
        )
        OutlinedTextField(state.rainfallNote, onRainfallNoteChange, label = { Text("Note") }, modifier = Modifier.fillMaxWidth())
        Button(onClick = onQueueRainfall, enabled = state.snapshot != null, modifier = Modifier.fillMaxWidth()) {
            Text("Queue Rainfall")
        }
        SectionTitle("Recent Rain")
        state.snapshot?.rainfall.orEmpty().take(20).forEach { rain ->
            EntityCard(rain.recordedOn, "${rain.mm} mm${rain.note?.let { " | $it" } ?: ""}")
        }
    }
}

@Composable
private fun MoveMobScreen(
    state: FieldUiState,
    onBackHome: () -> Unit,
    onMobSelected: (String) -> Unit,
    onPaddockSelected: (String) -> Unit,
    onMoveNoteChange: (String) -> Unit,
    onQueueMobMove: () -> Unit,
) {
    FormScaffold("Move Mob", onBackHome) {
        MobPicker(state.snapshot?.mobs.orEmpty(), state.selectedMobId, onMobSelected)
        PaddockPicker(state.snapshot?.paddocks.orEmpty(), state.selectedPaddockId, onPaddockSelected)
        OutlinedTextField(state.moveNote, onMoveNoteChange, label = { Text("Move note") }, modifier = Modifier.fillMaxWidth())
        Button(onClick = onQueueMobMove, enabled = state.snapshot != null, modifier = Modifier.fillMaxWidth()) {
            Text("Queue Move")
        }
    }
}

@Composable
private fun StockCountScreen(
    state: FieldUiState,
    onBackHome: () -> Unit,
    onMobSelected: (String) -> Unit,
    onAnimalGroupSelected: (String) -> Unit,
    onStockQuantityChange: (String) -> Unit,
    onStockNoteChange: (String) -> Unit,
    onQueueStockCount: () -> Unit,
) {
    FormScaffold("Stock Count", onBackHome) {
        MobPicker(state.snapshot?.mobs.orEmpty(), state.selectedMobId, onMobSelected)
        AnimalGroupPicker(state.animalGroupTypes, state.selectedAnimalGroupTypeId, onAnimalGroupSelected)
        OutlinedTextField(
            state.stockQuantity,
            onStockQuantityChange,
            label = { Text("Head count") },
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
            modifier = Modifier.fillMaxWidth(),
        )
        OutlinedTextField(state.stockNote, onStockNoteChange, label = { Text("Adjustment note") }, modifier = Modifier.fillMaxWidth())
        Button(onClick = onQueueStockCount, enabled = state.snapshot != null, modifier = Modifier.fillMaxWidth()) {
            Text("Queue Count")
        }
    }
}

@Composable
private fun TransferMobScreen(
    state: FieldUiState,
    onBackHome: () -> Unit,
    onMobSelected: (String) -> Unit,
    onTransferDestinationChange: (String) -> Unit,
    onAnimalGroupSelected: (String) -> Unit,
    onTransferQuantityChange: (String) -> Unit,
    onTransferNoteChange: (String) -> Unit,
    onQueueMobTransfer: () -> Unit,
) {
    FormScaffold("Transfer Stock", onBackHome) {
        MobPicker(state.snapshot?.mobs.orEmpty(), state.selectedMobId, onMobSelected, label = "Source mob")
        MobPicker(
            state.snapshot?.mobs.orEmpty().filter { it.id != state.selectedMobId },
            state.transferDestinationMobId,
            onTransferDestinationChange,
            label = "Destination mob",
        )
        AnimalGroupPicker(state.animalGroupTypes, state.selectedAnimalGroupTypeId, onAnimalGroupSelected)
        OutlinedTextField(
            state.transferQuantity,
            onTransferQuantityChange,
            label = { Text("Quantity") },
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
            modifier = Modifier.fillMaxWidth(),
        )
        OutlinedTextField(state.transferNote, onTransferNoteChange, label = { Text("Transfer note") }, modifier = Modifier.fillMaxWidth())
        Button(onClick = onQueueMobTransfer, enabled = state.snapshot != null, modifier = Modifier.fillMaxWidth()) {
            Text("Queue Transfer")
        }
    }
}

@Composable
private fun PaddockEditScreen(
    state: FieldUiState,
    onBackHome: () -> Unit,
    onPaddockStatusChange: (String) -> Unit,
    onPaddockNotesChange: (String) -> Unit,
    onPaddockTagsChange: (String) -> Unit,
    onQueuePaddockUpdate: () -> Unit,
) {
    FormScaffold("Edit Paddock", onBackHome) {
        Text(selectedPaddock(state)?.name ?: "Paddock", fontWeight = FontWeight.SemiBold)
        OutlinedTextField(state.paddockStatus, onPaddockStatusChange, label = { Text("Status") }, modifier = Modifier.fillMaxWidth())
        OutlinedTextField(state.paddockNotes, onPaddockNotesChange, label = { Text("Notes") }, modifier = Modifier.fillMaxWidth())
        OutlinedTextField(state.paddockTags, onPaddockTagsChange, label = { Text("Tags") }, modifier = Modifier.fillMaxWidth())
        Button(onClick = onQueuePaddockUpdate, enabled = state.selectedPaddockId.isNotBlank(), modifier = Modifier.fillMaxWidth()) {
            Text("Queue Paddock Update")
        }
    }
}

@Composable
private fun WaterEditScreen(
    state: FieldUiState,
    onBackHome: () -> Unit,
    onWaterStatusChange: (String) -> Unit,
    onWaterLevelChange: (String) -> Unit,
    onWaterActiveChange: (Boolean) -> Unit,
    onQueueWaterUpdate: () -> Unit,
) {
    FormScaffold("Update Water", onBackHome) {
        Text(selectedWaterAsset(state)?.name ?: "Water asset", fontWeight = FontWeight.SemiBold)
        Row(verticalAlignment = Alignment.CenterVertically) {
            Checkbox(checked = state.waterActive, onCheckedChange = onWaterActiveChange)
            Text("Active")
        }
        OutlinedTextField(state.waterStatus, onWaterStatusChange, label = { Text("Status") }, modifier = Modifier.fillMaxWidth())
        OutlinedTextField(state.waterLevel, onWaterLevelChange, label = { Text("Water level") }, modifier = Modifier.fillMaxWidth())
        Button(onClick = onQueueWaterUpdate, enabled = state.selectedWaterAssetId.isNotBlank(), modifier = Modifier.fillMaxWidth()) {
            Text("Queue Water Update")
        }
    }
}

@Composable
private fun TaskCreateScreen(
    state: FieldUiState,
    onBackHome: () -> Unit,
    onTaskHeadingChange: (String) -> Unit,
    onTaskDescriptionChange: (String) -> Unit,
    onTaskDueDateChange: (String) -> Unit,
    onQueueTaskCreate: () -> Unit,
) {
    FormScaffold("New Task", onBackHome) {
        Text("Linked to ${state.taskEntityType}", color = Color(0xFF516052))
        OutlinedTextField(state.taskHeading, onTaskHeadingChange, label = { Text("Heading") }, modifier = Modifier.fillMaxWidth())
        OutlinedTextField(state.taskDescription, onTaskDescriptionChange, label = { Text("Description") }, modifier = Modifier.fillMaxWidth())
        OutlinedTextField(state.taskDueDate, onTaskDueDateChange, label = { Text("Due date") }, modifier = Modifier.fillMaxWidth())
        Button(onClick = onQueueTaskCreate, enabled = state.taskHeading.isNotBlank(), modifier = Modifier.fillMaxWidth()) {
            Text("Queue Task")
        }
    }
}

@Composable
private fun SyncScreen(
    state: FieldUiState,
    onBackHome: () -> Unit,
    onSyncIntervalChange: (String) -> Unit,
    onSaveSyncInterval: () -> Unit,
    onSync: () -> Unit,
) {
    FormScaffold("Sync", onBackHome) {
        OutlinedTextField(
            state.syncIntervalText,
            onSyncIntervalChange,
            label = { Text("Foreground sync interval minutes") },
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
            modifier = Modifier.fillMaxWidth(),
        )
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp), modifier = Modifier.fillMaxWidth()) {
            Button(onClick = onSaveSyncInterval, modifier = Modifier.weight(1f)) { Text("Save") }
            OutlinedButton(onClick = onSync, modifier = Modifier.weight(1f)) { Text("Sync Now") }
        }
        state.failedCommands.forEach { failure ->
            EntityCard(
                title = failure.type,
                detail = "${failure.lastError ?: "failed"} | retry ${failure.retryCount}",
            )
        }
        state.lastSync?.results.orEmpty().take(10).forEach { result -> SyncResultRow(result) }
    }
}

@Composable
private fun MobPicker(
    mobs: List<MobSummary>,
    selectedMobId: String,
    onMobSelected: (String) -> Unit,
    label: String = "Mob",
) {
    var expanded by remember { mutableStateOf(false) }
    val selectedMob = mobs.firstOrNull { it.id == selectedMobId } ?: mobs.firstOrNull()
    Picker(label, selectedMob?.name ?: "No mobs", mobs.isNotEmpty(), expanded, { expanded = it }) {
        mobs.forEach { mob ->
            DropdownMenuItem(text = { Text(mob.name) }, onClick = { expanded = false; onMobSelected(mob.id) })
        }
    }
}

@Composable
private fun PaddockPicker(
    paddocks: List<PaddockSummary>,
    selectedPaddockId: String,
    onPaddockSelected: (String) -> Unit,
) {
    var expanded by remember { mutableStateOf(false) }
    val selected = paddocks.firstOrNull { it.id == selectedPaddockId } ?: paddocks.firstOrNull()
    Picker("Paddock", selected?.name ?: "No paddocks", paddocks.isNotEmpty(), expanded, { expanded = it }) {
        paddocks.forEach { paddock ->
            DropdownMenuItem(text = { Text(paddock.name) }, onClick = { expanded = false; onPaddockSelected(paddock.id) })
        }
    }
}

@Composable
private fun AnimalGroupPicker(
    animalGroupTypes: List<AnimalGroupTypeSummary>,
    selectedAnimalGroupTypeId: String,
    onAnimalGroupSelected: (String) -> Unit,
) {
    var expanded by remember { mutableStateOf(false) }
    val selected = animalGroupTypes.firstOrNull { it.id == selectedAnimalGroupTypeId } ?: animalGroupTypes.firstOrNull()
    Picker("Animal group", selected?.label ?: "No animal groups", animalGroupTypes.isNotEmpty(), expanded, { expanded = it }) {
        animalGroupTypes.forEach { group ->
            DropdownMenuItem(text = { Text(group.label) }, onClick = { expanded = false; onAnimalGroupSelected(group.id) })
        }
    }
}

@Composable
private fun Picker(
    label: String,
    selectedText: String,
    enabled: Boolean,
    expanded: Boolean,
    setExpanded: (Boolean) -> Unit,
    menu: @Composable () -> Unit,
) {
    Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
        FieldLabel(label)
        Box {
            OutlinedButton(onClick = { setExpanded(true) }, enabled = enabled, modifier = Modifier.fillMaxWidth()) {
                Text(selectedText)
            }
            DropdownMenu(expanded = expanded, onDismissRequest = { setExpanded(false) }) {
                menu()
            }
        }
    }
}

@Composable
private fun RelatedTasks(tasks: List<TaskSummary>, entityType: String, entityId: String) {
    val linked = tasks.filter { it.isLinkedTo(entityType, entityId) }
    if (linked.isEmpty()) {
        Text("No linked tasks", style = MaterialTheme.typography.bodySmall, color = Color(0xFF516052))
    } else {
        linked.take(4).forEach { task ->
            Text("${task.displayKey}: ${task.heading}", style = MaterialTheme.typography.bodySmall)
        }
    }
}

@Composable
private fun SectionCard(title: String, content: @Composable ColumnScope.() -> Unit) {
    Surface(
        color = MaterialTheme.colorScheme.surface,
        shape = RoundedCornerShape(8.dp),
        border = BorderStroke(1.dp, MaterialTheme.colorScheme.outline),
        modifier = Modifier.fillMaxWidth(),
    ) {
        Column(modifier = Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
            SectionTitle(title)
            content()
        }
    }
}

@Composable
private fun EntityCard(title: String, detail: String) {
    Surface(
        color = MaterialTheme.colorScheme.surface,
        shape = RoundedCornerShape(8.dp),
        border = BorderStroke(1.dp, MaterialTheme.colorScheme.outline),
        modifier = Modifier.fillMaxWidth(),
    ) {
        Column(modifier = Modifier.padding(10.dp), verticalArrangement = Arrangement.spacedBy(3.dp)) {
            Text(title, fontWeight = FontWeight.SemiBold)
            if (detail.isNotBlank()) {
                Text(detail, style = MaterialTheme.typography.bodySmall, color = Color(0xFF516052))
            }
        }
    }
}

@Composable
private fun MetricRows(metrics: List<Pair<String, String>>) {
    metrics.chunked(2).forEach { row ->
        Row(horizontalArrangement = Arrangement.spacedBy(10.dp), modifier = Modifier.fillMaxWidth()) {
            row.forEach { (label, value) ->
                MetricTile(label, value, Modifier.weight(1f))
            }
            if (row.size == 1) {
                Box(modifier = Modifier.weight(1f))
            }
        }
    }
}

@Composable
private fun MetricTile(label: String, value: String, modifier: Modifier = Modifier) {
    Surface(
        modifier = modifier,
        color = MaterialTheme.colorScheme.surfaceVariant,
        shape = RoundedCornerShape(8.dp),
        border = BorderStroke(1.dp, MaterialTheme.colorScheme.outline),
    ) {
        Column(modifier = Modifier.padding(10.dp), verticalArrangement = Arrangement.spacedBy(3.dp)) {
            Text(label, style = MaterialTheme.typography.labelMedium, color = Color(0xFF516052))
            Text(value, style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.SemiBold)
        }
    }
}

@Composable
private fun FormScaffold(title: String, onBackHome: () -> Unit, content: @Composable ColumnScope.() -> Unit) {
    Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
        OutlinedButton(onClick = onBackHome) { Text("Back") }
        SectionTitle(title)
        content()
    }
}

@Composable
private fun SyncResultRow(result: SyncResult) {
    val color = if (result.status == "applied") Color(0xFFEAF2E6) else Color(0xFFF8EAE4)
    Surface(color = color, shape = RoundedCornerShape(8.dp), border = BorderStroke(1.dp, MaterialTheme.colorScheme.outline)) {
        Column(modifier = Modifier.padding(10.dp), verticalArrangement = Arrangement.spacedBy(2.dp)) {
            Text("${result.type} - ${result.status}", fontWeight = FontWeight.SemiBold)
            result.errorMessage?.let { Text(it, style = MaterialTheme.typography.bodySmall, color = Color(0xFF7A3424)) }
        }
    }
}

@Composable
private fun SectionTitle(text: String) {
    Text(text, style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.SemiBold, color = Color(0xFF233126))
}

@Composable
private fun FieldLabel(text: String) {
    Text(text, style = MaterialTheme.typography.labelLarge, color = Color(0xFF516052))
}

@Composable
private fun SectionDivider() {
    HorizontalDivider(color = MaterialTheme.colorScheme.outline.copy(alpha = 0.7f))
}

private fun loginLoaded(state: FieldUiState, result: LoginLoadResult): FieldUiState {
    val snapshot = result.snapshot
        ?: return state.copy(
            currentScreen = AppScreen.Home,
            isAuthenticated = true,
            selectedFarm = null,
            snapshot = null,
            selectedMobId = "",
            pendingCount = 0,
            password = "",
            statusMessage = "Login worked, but this user has no farm access yet.",
        )
    return FieldUiState.fromSnapshot(
        snapshot = snapshot,
        pendingCount = state.pendingCount,
        failedCommands = state.failedCommands,
        syncIntervalMinutes = state.syncIntervalMinutes,
    ).copy(
        baseUrl = state.baseUrl,
        email = state.email,
        password = "",
        isAuthenticated = true,
        animalGroupTypes = mergeAnimalGroupTypes(result.bootstrap.animalGroupTypes, animalGroupTypesFromSnapshot(snapshot)),
        statusMessage = "Loaded ${snapshot.farm.name}",
    )
}

private fun loggedOut(state: FieldUiState, result: LogoutResult): FieldUiState {
    val message = if (result.errorMessage == null) {
        "Logged out."
    } else {
        "Logged out on this device. Server logout did not complete: ${result.errorMessage}"
    }
    return FieldUiState(
        baseUrl = state.baseUrl,
        email = state.email,
        pendingCount = state.pendingCount,
        failedCommands = state.failedCommands,
        syncIntervalMinutes = state.syncIntervalMinutes,
        syncIntervalText = state.syncIntervalText,
        statusMessage = message,
    )
}

private fun refreshed(state: FieldUiState, snapshot: FarmSnapshot): FieldUiState =
    FieldUiState.fromSnapshot(
        snapshot = snapshot,
        pendingCount = state.pendingCount,
        failedCommands = state.failedCommands,
        syncIntervalMinutes = state.syncIntervalMinutes,
    ).copy(
        baseUrl = state.baseUrl,
        email = state.email,
        isAuthenticated = state.isAuthenticated,
        statusMessage = "Refreshed ${snapshot.farm.name}",
    )

private fun synced(state: FieldUiState, summary: SyncSummary): FieldUiState {
    val base = summary.refreshedSnapshot?.let {
        FieldUiState.fromSnapshot(
            snapshot = it,
            pendingCount = summary.remainingQueueCount,
            failedCommands = state.failedCommands,
            syncIntervalMinutes = state.syncIntervalMinutes,
        )
    } ?: state.copy(pendingCount = summary.remainingQueueCount)
    return base.copy(
        baseUrl = state.baseUrl,
        email = state.email,
        isAuthenticated = state.isAuthenticated,
        lastSync = summary,
        statusMessage = "Sync applied ${summary.appliedCount}, failed ${summary.failedCount}. Pending: ${summary.remainingQueueCount}",
    )
}

private fun selectMob(state: FieldUiState, mobId: String): FieldUiState {
    val balance = state.snapshot?.mobs?.firstOrNull { it.id == mobId }?.balances?.firstOrNull()
    val destination = state.snapshot?.mobs?.firstOrNull { it.id != mobId }?.id.orEmpty()
    return state.copy(
        selectedMobId = mobId,
        transferDestinationMobId = destination,
        selectedAnimalGroupTypeId = balance?.animalGroupTypeId
            ?: state.selectedAnimalGroupTypeId.ifBlank { state.animalGroupTypes.firstOrNull()?.id.orEmpty() },
        stockQuantity = balance?.headCount?.toString() ?: state.stockQuantity,
    )
}

private fun selectPaddock(state: FieldUiState, paddockId: String): FieldUiState {
    val paddock = state.snapshot?.paddocks?.firstOrNull { it.id == paddockId }
    return state.copy(
        selectedPaddockId = paddockId,
        paddockStatus = paddock?.status.orEmpty(),
        paddockNotes = paddock?.notes.orEmpty(),
        paddockTags = paddock?.tagLabel.orEmpty(),
    )
}

private fun selectWaterAsset(state: FieldUiState, assetId: String): FieldUiState {
    val asset = state.snapshot?.waterAssets?.firstOrNull { it.id == assetId }
    return state.copy(
        selectedWaterAssetId = assetId,
        waterStatus = asset?.status.orEmpty(),
        waterLevel = asset?.waterLevel.orEmpty(),
        waterActive = asset?.active ?: true,
    )
}

private fun selectAnimalGroup(state: FieldUiState, groupTypeId: String): FieldUiState {
    val currentBalance = selectedMob(state)?.balances?.firstOrNull { it.animalGroupTypeId == groupTypeId }
    return state.copy(selectedAnimalGroupTypeId = groupTypeId, stockQuantity = currentBalance?.headCount?.toString() ?: state.stockQuantity)
}

private fun queueRainfall(state: FieldUiState, repo: MobileRepository): FieldUiState {
    val farm = state.selectedFarm ?: return state.copy(statusMessage = "Load a farm snapshot before queueing rainfall.")
    val recordedOn = runCatching { LocalDate.parse(state.rainfallDate).toString() }
        .getOrElse { return state.copy(statusMessage = "Date must use YYYY-MM-DD.") }
    val mm = state.rainfallMm.toDoubleOrNull() ?: return state.copy(statusMessage = "Rainfall must be a number.")
    if (mm < 0) return state.copy(statusMessage = "Rainfall must be zero or more.")
    repo.queueRainfall(farm.id, recordedOn, mm, state.rainfallNote)
    return state.copy(currentScreen = AppScreen.Home, rainfallMm = "", rainfallNote = "", statusMessage = "Queued rainfall.")
}

private fun queueMobMove(state: FieldUiState, repo: MobileRepository): FieldUiState {
    val farm = state.selectedFarm ?: return state.copy(statusMessage = "Load a farm snapshot before queueing a mob move.")
    val mobId = state.selectedMobId.ifBlank { state.snapshot?.mobs?.firstOrNull()?.id.orEmpty() }
    val paddockId = state.selectedPaddockId.ifBlank { state.snapshot?.paddocks?.firstOrNull()?.id.orEmpty() }
    if (mobId.isBlank() || paddockId.isBlank()) return state.copy(statusMessage = "Choose a mob and destination paddock.")
    repo.queueMobMove(farm.id, mobId, paddockId, state.moveNote)
    return state.copy(currentScreen = AppScreen.Home, moveNote = "", statusMessage = "Queued mob move.")
}

private fun queueStockCount(state: FieldUiState, repo: MobileRepository): FieldUiState {
    val farm = state.selectedFarm ?: return state.copy(statusMessage = "Load a farm snapshot before queueing a stock count.")
    val quantity = state.stockQuantity.toIntOrNull() ?: return state.copy(statusMessage = "Head count must be a whole number.")
    if (quantity < 0) return state.copy(statusMessage = "Head count must be zero or more.")
    val mobId = state.selectedMobId.ifBlank { state.snapshot?.mobs?.firstOrNull()?.id.orEmpty() }
    val groupId = state.selectedAnimalGroupTypeId.ifBlank { selectedMob(state)?.balances?.firstOrNull()?.animalGroupTypeId.orEmpty() }
    if (mobId.isBlank() || groupId.isBlank()) return state.copy(statusMessage = "Choose a mob and animal group.")
    repo.queueStockCount(farm.id, mobId, groupId, quantity, state.stockNote)
    return state.copy(currentScreen = AppScreen.Home, stockNote = "", statusMessage = "Queued stock count.")
}

private fun queueMobTransfer(state: FieldUiState, repo: MobileRepository): FieldUiState {
    val farm = state.selectedFarm ?: return state.copy(statusMessage = "Load a farm snapshot before queueing a transfer.")
    val quantity = state.transferQuantity.toIntOrNull() ?: return state.copy(statusMessage = "Transfer quantity must be a whole number.")
    if (quantity <= 0) return state.copy(statusMessage = "Transfer quantity must be greater than zero.")
    if (state.selectedMobId.isBlank() || state.transferDestinationMobId.isBlank() || state.selectedAnimalGroupTypeId.isBlank()) {
        return state.copy(statusMessage = "Choose source, destination, and animal group.")
    }
    repo.queueMobTransfer(
        farm.id,
        state.selectedMobId,
        state.transferDestinationMobId,
        state.selectedAnimalGroupTypeId,
        quantity,
        state.transferNote,
    )
    return state.copy(currentScreen = AppScreen.Home, transferQuantity = "", transferNote = "", statusMessage = "Queued stock transfer.")
}

private fun queuePaddockUpdate(state: FieldUiState, repo: MobileRepository): FieldUiState {
    val farm = state.selectedFarm ?: return state.copy(statusMessage = "Load a farm snapshot before editing a paddock.")
    if (state.selectedPaddockId.isBlank()) return state.copy(statusMessage = "Choose a paddock.")
    repo.queuePaddockUpdate(farm.id, state.selectedPaddockId, state.paddockStatus, state.paddockNotes, state.paddockTags)
    return state.copy(currentScreen = AppScreen.Home, statusMessage = "Queued paddock update.")
}

private fun queueWaterUpdate(state: FieldUiState, repo: MobileRepository): FieldUiState {
    val farm = state.selectedFarm ?: return state.copy(statusMessage = "Load a farm snapshot before editing water.")
    if (state.selectedWaterAssetId.isBlank()) return state.copy(statusMessage = "Choose a water asset.")
    repo.queueWaterStatus(farm.id, state.selectedWaterAssetId, state.waterStatus, state.waterLevel, state.waterActive)
    return state.copy(currentScreen = AppScreen.Home, statusMessage = "Queued water update.")
}

private fun queueTaskCreate(state: FieldUiState, repo: MobileRepository): FieldUiState {
    val farm = state.selectedFarm ?: return state.copy(statusMessage = "Load a farm snapshot before creating a task.")
    if (state.taskHeading.isBlank()) return state.copy(statusMessage = "Task heading is required.")
    repo.queueTaskCreate(
        farm.id,
        state.taskHeading,
        state.taskDescription.ifBlank { state.taskHeading },
        state.taskDueDate,
        state.taskEntityType,
        state.taskEntityId,
    )
    return state.copy(currentScreen = AppScreen.Home, taskHeading = "", taskDescription = "", statusMessage = "Queued task.")
}

private fun queueTaskStatus(state: FieldUiState, repo: MobileRepository, task: TaskSummary, status: String): FieldUiState {
    val farm = state.selectedFarm ?: return state.copy(statusMessage = "Load a farm snapshot before updating a task.")
    repo.queueTaskStatus(farm.id, task.id, status, "Mobile shortcut")
    return state.copy(statusMessage = "Queued ${task.displayKey} status update.")
}

private fun queueTaskComment(state: FieldUiState, repo: MobileRepository, task: TaskSummary): FieldUiState {
    val farm = state.selectedFarm ?: return state.copy(statusMessage = "Load a farm snapshot before commenting.")
    if (state.taskComment.isBlank()) return state.copy(statusMessage = "Task note is required.")
    repo.queueTaskComment(farm.id, task.id, state.taskComment)
    return state.copy(taskComment = "", statusMessage = "Queued task note.")
}

private fun selectedMob(state: FieldUiState): MobSummary? =
    state.snapshot?.mobs?.firstOrNull { it.id == state.selectedMobId } ?: state.snapshot?.mobs?.firstOrNull()

private fun selectedPaddock(state: FieldUiState): PaddockSummary? =
    state.snapshot?.paddocks?.firstOrNull { it.id == state.selectedPaddockId }

private fun selectedWaterAsset(state: FieldUiState): WaterAssetSummary? =
    state.snapshot?.waterAssets?.firstOrNull { it.id == state.selectedWaterAssetId }

private fun animalGroupTypesFromSnapshot(snapshot: FarmSnapshot?): List<AnimalGroupTypeSummary> {
    if (snapshot == null) return emptyList()
    return mergeAnimalGroupTypes(emptyList(), snapshot.mobs.flatMap { mob -> mob.balances.map { it.animalGroupType } })
}

private fun mergeAnimalGroupTypes(
    primary: List<AnimalGroupTypeSummary>,
    secondary: List<AnimalGroupTypeSummary>,
): List<AnimalGroupTypeSummary> {
    val byId = linkedMapOf<String, AnimalGroupTypeSummary>()
    (primary + secondary).filter { it.id.isNotBlank() }.forEach { group -> byId.putIfAbsent(group.id, group) }
    return byId.values.toList()
}
