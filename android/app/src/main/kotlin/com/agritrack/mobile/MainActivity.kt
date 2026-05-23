package com.agritrack.mobile

import android.content.Context
import android.graphics.Bitmap
import android.net.ConnectivityManager
import android.net.Network
import android.net.NetworkCapabilities
import android.net.NetworkRequest
import android.net.Uri
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.provider.OpenableColumns
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.clickable
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.gestures.detectTransformGestures
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
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
import androidx.compose.material3.TextButton
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.IntSize
import androidx.compose.ui.unit.dp
import androidx.compose.ui.layout.onSizeChanged
import com.agritrack.mobile.data.AnimalGroupTypeSummary
import com.agritrack.mobile.data.CalendarFilterState
import com.agritrack.mobile.data.CalendarItemSummary
import com.agritrack.mobile.data.DecisionItemSummary
import com.agritrack.mobile.data.FarmSnapshot
import com.agritrack.mobile.data.FarmSummary
import com.agritrack.mobile.data.LocalFieldStore
import com.agritrack.mobile.data.LoginLoadResult
import com.agritrack.mobile.data.LogoutResult
import com.agritrack.mobile.data.MapFeatureSummary
import com.agritrack.mobile.data.MobileFormOptions
import com.agritrack.mobile.data.MobileOption
import com.agritrack.mobile.data.MobFilterState
import com.agritrack.mobile.data.MobSummary
import com.agritrack.mobile.data.MobileApiClient
import com.agritrack.mobile.data.MobileRepository
import com.agritrack.mobile.data.OutboxFailure
import com.agritrack.mobile.data.PaddockFilterState
import com.agritrack.mobile.data.PaddockMobSummary
import com.agritrack.mobile.data.PaddockSummary
import com.agritrack.mobile.data.SecureTokenStore
import com.agritrack.mobile.data.SyncResult
import com.agritrack.mobile.data.SyncSummary
import com.agritrack.mobile.data.TaskSummary
import com.agritrack.mobile.data.WaterFilterState
import com.agritrack.mobile.data.WaterAssetSummary
import com.agritrack.mobile.data.filterCalendarItems
import com.agritrack.mobile.data.filterMobs
import com.agritrack.mobile.data.filterPaddocks
import com.agritrack.mobile.data.filterWaterAssets
import com.agritrack.mobile.data.mobGrazingPaddocks
import com.agritrack.mobile.data.paddockGrazing
import com.agritrack.mobile.data.paddockMapFeature
import com.agritrack.mobile.data.paddockStockLines
import com.agritrack.mobile.data.paddockWaterAvailability
import com.agritrack.mobile.data.servedPaddockNames
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.io.FileOutputStream
import java.time.Instant
import java.time.LocalDate
import java.util.concurrent.ExecutorService
import java.util.concurrent.Executors
import kotlin.math.abs
import kotlin.math.max
import kotlin.math.min

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

            var pendingPhotoTarget by remember { mutableStateOf<PhotoTarget?>(null) }
            var pendingImageChoice by remember { mutableStateOf<ImageChoiceTarget?>(null) }

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
                                .copy(
                                    isBusy = false,
                                    connectionState = BackendConnectionState.Online,
                                    failedCommands = fieldStore.failedCommands(),
                                )
                        }
                    } catch (exc: Exception) {
                        val nextConnectionState = if ((exc.message ?: "").contains("token", ignoreCase = true)) {
                            BackendConnectionState.AuthError
                        } else {
                            BackendConnectionState.Offline
                        }
                        main.post {
                            uiState = uiState.copy(
                                isBusy = false,
                                connectionState = nextConnectionState,
                                pendingCount = fieldStore.pendingCount(),
                                failedCommands = fieldStore.failedCommands(),
                                statusMessage = exc.message ?: "Operation failed",
                            )
                        }
                    }
                }
            }

            fun queuePhotoFile(target: PhotoTarget, photo: LocalPhotoFile) {
                runTask(
                    statusMessage = "Queueing task photo...",
                    work = { repo ->
                        repo.queueTaskPhoto(
                            farmId = target.farmId,
                            serverTaskId = target.serverTaskId,
                            targetClientCommandId = target.targetClientCommandId,
                            filePath = photo.filePath,
                            originalFilename = photo.originalFilename,
                            contentType = photo.contentType,
                            byteSize = photo.byteSize,
                            caption = null,
                            capturedAt = Instant.now().toString(),
                        )
                    },
                    reduce = { state, _ ->
                        state.copy(
                            pendingCount = fieldStore.pendingCount(),
                            statusMessage = "Queued task photo.",
                        )
                    },
                )
            }

            val photoPickerLauncher = rememberLauncherForActivityResult(ActivityResultContracts.GetContent()) { uri: Uri? ->
                val target = pendingPhotoTarget
                pendingPhotoTarget = null
                if (uri != null && target != null) {
                    runCatching { copyImageUriToLocalPhoto(uri) }
                        .onSuccess { queuePhotoFile(target, it) }
                        .onFailure { exc -> uiState = uiState.copy(statusMessage = exc.message ?: "Photo could not be queued.") }
                }
            }

            val cameraLauncher = rememberLauncherForActivityResult(ActivityResultContracts.TakePicturePreview()) { bitmap: Bitmap? ->
                val target = pendingPhotoTarget
                pendingPhotoTarget = null
                if (bitmap != null && target != null) {
                    runCatching { saveBitmapToLocalPhoto(bitmap) }
                        .onSuccess { queuePhotoFile(target, it) }
                        .onFailure { exc -> uiState = uiState.copy(statusMessage = exc.message ?: "Photo could not be queued.") }
                }
            }

            fun attachNewTaskPhoto(capture: Boolean) {
                val farm = uiState.selectedFarm
                if (farm == null) {
                    uiState = uiState.copy(statusMessage = "Load a farm snapshot before creating a task.")
                } else if (uiState.taskHeading.isBlank()) {
                    uiState = uiState.copy(statusMessage = "Task heading is required.")
                } else {
                    val commandId = repository().queueTaskCreate(
                        farm.id,
                        uiState.taskHeading,
                        uiState.taskDescription.ifBlank { uiState.taskHeading },
                        uiState.taskDueDate,
                        uiState.taskEntityType,
                        uiState.taskEntityId,
                    )
                    uiState = uiState.copy(
                        pendingCount = fieldStore.pendingCount(),
                        currentScreen = AppScreen.Home,
                        taskHeading = "",
                        taskDescription = "",
                        statusMessage = "Queued task. Attach the photo next.",
                    )
                    pendingPhotoTarget = PhotoTarget(farm.id, null, commandId)
                    if (capture) {
                        cameraLauncher.launch(null)
                    } else {
                        photoPickerLauncher.launch("image/*")
                    }
                }
            }

            fun attachTaskPhoto(task: TaskSummary, capture: Boolean) {
                val farm = uiState.selectedFarm
                if (farm == null) {
                    uiState = uiState.copy(statusMessage = "Load a farm snapshot before attaching a photo.")
                } else {
                    pendingPhotoTarget = PhotoTarget(farm.id, task.id, null)
                    if (capture) {
                        cameraLauncher.launch(null)
                    } else {
                        photoPickerLauncher.launch("image/*")
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

            fun checkConnection() {
                if (!uiState.isAuthenticated || uiState.isBusy) {
                    return
                }
                runTask(
                    statusMessage = "Checking backend connection...",
                    work = { repo -> repo.ping() },
                    reduce = { state, _ ->
                        state.copy(
                            connectionState = BackendConnectionState.Online,
                            statusMessage = "Backend connection active.",
                        )
                    },
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
                    checkConnection()
                    syncNow("Connection restored. Syncing queued field edits...", automatic = true)
                }
            }

            LaunchedEffect(uiState.isAuthenticated) {
                if (uiState.isAuthenticated) {
                    checkConnection()
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
                    onTaskSelected = { uiState = uiState.copy(selectedTaskId = it, currentScreen = AppScreen.TaskDetail) },
                    onCalendarItemSelected = { item ->
                        if (item.kind == "task" && item.taskId != null) {
                            uiState = uiState.copy(selectedTaskId = item.taskId, currentScreen = AppScreen.TaskDetail)
                        } else {
                            uiState = uiState.copy(
                                selectedCalendarItemSourceId = item.sourceId.orEmpty(),
                                selectedCalendarItemDate = item.date,
                                currentScreen = AppScreen.CalendarItemDetail,
                            )
                        }
                    },
                    onAttachNewTaskPhoto = { pendingImageChoice = ImageChoiceTarget(newTask = true) },
                    onAttachTaskPhoto = { task -> pendingImageChoice = ImageChoiceTarget(task = task) },
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
                pendingImageChoice?.let { target ->
                    AlertDialog(
                        onDismissRequest = { pendingImageChoice = null },
                        title = { Text("Add Image") },
                        text = { Text("Choose an image source for this task.") },
                        confirmButton = {
                            TextButton(
                                onClick = {
                                    pendingImageChoice = null
                                    if (target.newTask) {
                                        attachNewTaskPhoto(true)
                                    } else {
                                        target.task?.let { attachTaskPhoto(it, true) }
                                    }
                                },
                            ) {
                                Text("Camera")
                            }
                        },
                        dismissButton = {
                            TextButton(
                                onClick = {
                                    pendingImageChoice = null
                                    if (target.newTask) {
                                        attachNewTaskPhoto(false)
                                    } else {
                                        target.task?.let { attachTaskPhoto(it, false) }
                                    }
                                },
                            ) {
                                Text("Choose Image")
                            }
                        },
                    )
                }
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

    private fun copyImageUriToLocalPhoto(uri: Uri): LocalPhotoFile {
        val contentType = contentResolver.getType(uri) ?: "image/jpeg"
        val filename = displayNameForUri(uri) ?: "field-photo-${System.currentTimeMillis()}.jpg"
        val target = taskPhotoFile(filename)
        contentResolver.openInputStream(uri).use { input ->
            requireNotNull(input) { "Photo could not be opened." }
            FileOutputStream(target).use { output -> input.copyTo(output) }
        }
        return LocalPhotoFile(
            filePath = target.absolutePath,
            originalFilename = filename,
            contentType = contentType,
            byteSize = target.length(),
        )
    }

    private fun saveBitmapToLocalPhoto(bitmap: Bitmap): LocalPhotoFile {
        val filename = "field-photo-${System.currentTimeMillis()}.jpg"
        val target = taskPhotoFile(filename)
        FileOutputStream(target).use { output ->
            bitmap.compress(Bitmap.CompressFormat.JPEG, 85, output)
        }
        return LocalPhotoFile(
            filePath = target.absolutePath,
            originalFilename = filename,
            contentType = "image/jpeg",
            byteSize = target.length(),
        )
    }

    private fun taskPhotoFile(filename: String): File {
        val dir = File(filesDir, "task_photos")
        if (!dir.exists()) {
            dir.mkdirs()
        }
        val safeName = filename.replace(Regex("[^A-Za-z0-9._-]"), "_")
        return File(dir, "${System.currentTimeMillis()}-$safeName")
    }

    private fun displayNameForUri(uri: Uri): String? {
        return contentResolver.query(uri, arrayOf(OpenableColumns.DISPLAY_NAME), null, null, null)
            ?.use { cursor ->
                if (cursor.moveToFirst()) cursor.getString(0) else null
            }
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
    val connectionState: BackendConnectionState = BackendConnectionState.Unknown,
    val statusMessage: String = "Ready",
    val selectedFarm: FarmSummary? = null,
    val snapshot: FarmSnapshot? = null,
    val pendingCount: Int = 0,
    val failedCommands: List<OutboxFailure> = emptyList(),
    val lastSync: SyncSummary? = null,
    val animalGroupTypes: List<AnimalGroupTypeSummary> = emptyList(),
    val formOptions: MobileFormOptions = defaultMobileFormOptions(),
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
    val selectedTaskId: String = "",
    val selectedCalendarItemSourceId: String = "",
    val selectedCalendarItemDate: String = "",
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
                formOptions = defaultMobileFormOptions(),
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
                selectedTaskId = snapshot?.tasks?.firstOrNull()?.id.orEmpty(),
                selectedCalendarItemSourceId = snapshot?.calendarItems?.firstOrNull()?.sourceId.orEmpty(),
                selectedCalendarItemDate = snapshot?.calendarItems?.firstOrNull()?.date.orEmpty(),
            )
        }
    }
}

private enum class BackendConnectionState {
    Unknown,
    Online,
    Offline,
    AuthError,
}

private data class PhotoTarget(
    val farmId: String,
    val serverTaskId: String?,
    val targetClientCommandId: String?,
)

private data class LocalPhotoFile(
    val filePath: String,
    val originalFilename: String,
    val contentType: String,
    val byteSize: Long,
)

private data class ImageChoiceTarget(
    val task: TaskSummary? = null,
    val newTask: Boolean = false,
)

private data class MapSelection(
    val feature: MapFeatureSummary,
    val mob: PaddockMobSummary? = null,
)

private enum class AppScreen {
    Home,
    Farm,
    FarmMap,
    FarmMapFullscreen,
    Calendar,
    Tasks,
    TaskDetail,
    CalendarItemDetail,
    Decisions,
    Mobs,
    MobDetail,
    Paddocks,
    PaddockDetail,
    WaterAssets,
    WaterAssetDetail,
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
    onTaskSelected: (String) -> Unit,
    onCalendarItemSelected: (CalendarItemSummary) -> Unit,
    onAttachNewTaskPhoto: () -> Unit,
    onAttachTaskPhoto: (TaskSummary) -> Unit,
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
        val isMapFullscreen = state.currentScreen == AppScreen.FarmMapFullscreen
        Column(
            modifier = Modifier
                .fillMaxSize()
                .then(if (isMapFullscreen) Modifier else Modifier.verticalScroll(rememberScrollState()))
                .padding(if (isMapFullscreen) 8.dp else 18.dp),
            verticalArrangement = Arrangement.spacedBy(if (isMapFullscreen) 8.dp else 16.dp),
        ) {
            if (!isMapFullscreen) {
                Header(state, onLogout)
            }
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
                AppScreen.FarmMap -> FarmMapScreen(
                    state,
                    onBackHome,
                    onPaddockSelected,
                    onMobSelected,
                    onWaterAssetSelected,
                    onOpenScreen,
                )
                AppScreen.FarmMapFullscreen -> FarmMapFullscreenScreen(
                    state,
                    onBackHome,
                    onPaddockSelected,
                    onMobSelected,
                    onWaterAssetSelected,
                    onOpenScreen,
                )
                AppScreen.Calendar -> CalendarScreen(
                    state,
                    onBackHome,
                    onOpenScreen,
                    onTaskSelected,
                    onCalendarItemSelected,
                    onQueueTaskStatus,
                    onTaskCommentChange,
                    onQueueTaskComment,
                    onAttachTaskPhoto,
                )
                AppScreen.Tasks -> TasksScreen(
                    state,
                    onBackHome,
                    onOpenScreen,
                    onTaskSelected,
                    onQueueTaskStatus,
                    onTaskCommentChange,
                    onQueueTaskComment,
                    onAttachTaskPhoto,
                )
                AppScreen.TaskDetail -> TaskDetailScreen(
                    state,
                    onBackHome,
                    onQueueTaskStatus,
                    onTaskCommentChange,
                    onQueueTaskComment,
                    onAttachTaskPhoto,
                )
                AppScreen.CalendarItemDetail -> CalendarItemDetailScreen(state, onBackHome)
                AppScreen.Decisions -> DecisionsScreen(state, onBackHome, onOpenScreen)
                AppScreen.Mobs -> MobsScreen(
                    state,
                    onBackHome,
                    onMobSelected,
                    onOpenScreen,
                )
                AppScreen.MobDetail -> MobDetailScreen(
                    state,
                    { onOpenScreen(AppScreen.Mobs) },
                    onMobSelected,
                    onPaddockSelected,
                    onOpenScreen,
                    onStartTaskForEntity,
                )
                AppScreen.Paddocks -> PaddocksScreen(
                    state,
                    onBackHome,
                    onPaddockSelected,
                    onOpenScreen,
                )
                AppScreen.PaddockDetail -> PaddockDetailScreen(
                    state,
                    { onOpenScreen(AppScreen.Paddocks) },
                    onPaddockSelected,
                    onMobSelected,
                    onOpenScreen,
                    onStartTaskForEntity,
                )
                AppScreen.WaterAssets -> WaterAssetsScreen(
                    state,
                    onBackHome,
                    onWaterAssetSelected,
                    onOpenScreen,
                )
                AppScreen.WaterAssetDetail -> WaterAssetDetailScreen(
                    state,
                    { onOpenScreen(AppScreen.WaterAssets) },
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
                    onAttachNewTaskPhoto,
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
            ConnectionBadge(state.connectionState)
            if (state.isAuthenticated) {
                OutlinedButton(onClick = onLogout, enabled = !state.isBusy) {
                    Text("Log out")
                }
            }
        }
    }
}

@Composable
private fun ConnectionBadge(connectionState: BackendConnectionState) {
    val (label, color) = when (connectionState) {
        BackendConnectionState.Online -> "Online" to Color(0xFFEAF2E6)
        BackendConnectionState.Offline -> "Offline" to Color(0xFFF8EAE4)
        BackendConnectionState.AuthError -> "Auth" to Color(0xFFFFE0E0)
        BackendConnectionState.Unknown -> "Check" to Color(0xFFFFF6DF)
    }
    Surface(
        color = color,
        shape = RoundedCornerShape(8.dp),
        border = BorderStroke(1.dp, MaterialTheme.colorScheme.outline),
    ) {
        Text(label, modifier = Modifier.padding(horizontal = 10.dp, vertical = 6.dp), fontWeight = FontWeight.SemiBold)
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
            FarmSummaryPanel(state, onRefresh, onOpenScreen)
            DecisionFeedPanel(state.snapshot?.decisionFeed.orEmpty(), onOpenScreen)
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
private fun FarmSummaryPanel(state: FieldUiState, onRefresh: () -> Unit, onOpenScreen: (AppScreen) -> Unit) {
    SectionCard("Current Farm") {
        val snapshot = state.snapshot
        if (snapshot == null) {
            Text("No farm loaded", color = Color(0xFF516052))
            return@SectionCard
        }
        Text(snapshot.farm.name, style = MaterialTheme.typography.titleLarge, fontWeight = FontWeight.Bold)
        Text(snapshot.farm.timezone, style = MaterialTheme.typography.bodySmall, color = Color(0xFF516052))
        MetricActionRows(
            listOf(
                MetricAction("Paddocks", snapshot.paddockCount.toString(), AppScreen.Paddocks),
                MetricAction("Mobs", snapshot.mobCount.toString(), AppScreen.Mobs),
                MetricAction("Water", snapshot.waterAssetCount.toString(), AppScreen.WaterAssets),
                MetricAction("Calendar", snapshot.calendarItemCount.toString(), AppScreen.Calendar),
                MetricAction("Decisions", snapshot.decisionCount.toString(), AppScreen.Decisions),
            )
        ) { onOpenScreen(it) }
        Button(onClick = { onOpenScreen(AppScreen.TaskCreate) }, enabled = !state.isBusy, modifier = Modifier.fillMaxWidth()) {
            Text("New Task")
        }
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
private fun DecisionFeedPanel(items: List<DecisionItemSummary>, onOpenScreen: (AppScreen) -> Unit) {
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
        if (items.isNotEmpty()) {
            OutlinedButton(onClick = { onOpenScreen(AppScreen.Decisions) }, modifier = Modifier.fillMaxWidth()) {
                Text("Open Decisions")
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
private fun FarmMapScreen(
    state: FieldUiState,
    onBackHome: () -> Unit,
    onPaddockSelected: (String) -> Unit,
    onMobSelected: (String) -> Unit,
    onWaterAssetSelected: (String) -> Unit,
    onOpenScreen: (AppScreen) -> Unit,
) {
    FormScaffold("Farm Map", onBackHome) {
        val snapshot = state.snapshot ?: return@FormScaffold
        FarmMapContent(
            snapshot = snapshot,
            fullscreen = false,
            mapModifier = Modifier.height(420.dp),
            onFullscreen = { onOpenScreen(AppScreen.FarmMapFullscreen) },
            onPaddockSelected = onPaddockSelected,
            onMobSelected = onMobSelected,
            onWaterAssetSelected = onWaterAssetSelected,
            onOpenScreen = onOpenScreen,
        )
    }
}

@Composable
private fun FarmMapFullscreenScreen(
    state: FieldUiState,
    onBackHome: () -> Unit,
    onPaddockSelected: (String) -> Unit,
    onMobSelected: (String) -> Unit,
    onWaterAssetSelected: (String) -> Unit,
    onOpenScreen: (AppScreen) -> Unit,
) {
    val snapshot = state.snapshot ?: return
    Column(modifier = Modifier.fillMaxSize(), verticalArrangement = Arrangement.spacedBy(8.dp)) {
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp), modifier = Modifier.fillMaxWidth()) {
            Button(onClick = onBackHome, modifier = Modifier.weight(1f)) { Text("Exit Map") }
            OutlinedButton(onClick = { onOpenScreen(AppScreen.FarmMap) }, modifier = Modifier.weight(1f)) {
                Text("Normal View")
            }
        }
        FarmMapContent(
            snapshot = snapshot,
            fullscreen = true,
            mapModifier = Modifier.weight(1f),
            onFullscreen = {},
            onPaddockSelected = onPaddockSelected,
            onMobSelected = onMobSelected,
            onWaterAssetSelected = onWaterAssetSelected,
            onOpenScreen = onOpenScreen,
        )
    }
}

@Composable
private fun FarmMapContent(
    snapshot: FarmSnapshot,
    fullscreen: Boolean,
    mapModifier: Modifier,
    onFullscreen: () -> Unit,
    onPaddockSelected: (String) -> Unit,
    onMobSelected: (String) -> Unit,
    onWaterAssetSelected: (String) -> Unit,
    onOpenScreen: (AppScreen) -> Unit,
) {
    var selectedMapItem by remember(snapshot.farm.id, fullscreen) { mutableStateOf<MapSelection?>(null) }
    snapshot.mapWarnings.forEach { warning -> Text(warning, color = Color(0xFF7A3424)) }
    if (!fullscreen) {
        OutlinedButton(onClick = onFullscreen, modifier = Modifier.fillMaxWidth()) {
            Text("Full Screen Map")
        }
    }
    OfflineFarmMap(
        features = snapshot.mapFeatures,
        fullscreen = fullscreen,
        modifier = mapModifier,
        onSelection = { selectedMapItem = it },
    )
    selectedMapItem?.let { selection ->
        MapSelectionPanel(
            selection = selection,
            onOpen = {
                val mob = selection.mob
                if (mob != null) {
                    onMobSelected(mob.mobId)
                    onOpenScreen(AppScreen.MobDetail)
                } else {
                    val waterAssetId = selection.feature.waterAssetId
                    val paddockId = selection.feature.paddockId
                    when {
                        waterAssetId != null -> {
                            onWaterAssetSelected(waterAssetId)
                            onOpenScreen(AppScreen.WaterAssetDetail)
                        }
                        paddockId != null -> {
                            onPaddockSelected(paddockId)
                            onOpenScreen(AppScreen.PaddockDetail)
                        }
                    }
                }
            },
        )
    }
    if (!fullscreen) {
        snapshot.mapFeatures
            .filter { it.featureType == "paddock" || it.featureType == "water_asset" }
            .take(12)
            .forEach { feature ->
                EntityCard(
                    title = feature.name,
                    detail = mapFeatureDetail(feature),
                )
            }
    }
}

@Composable
private fun MapSelectionPanel(selection: MapSelection, onOpen: () -> Unit) {
    val feature = selection.feature
    val title = selection.mob?.mobName ?: feature.name
    val detail = if (selection.mob != null) {
        "${feature.name} | ${selection.mob.allocationPct}% allocation"
    } else {
        mapFeatureDetail(feature)
    }
    SectionCard(title) {
        Text(detail.ifBlank { feature.featureType }, color = Color(0xFF516052))
        Button(onClick = onOpen, modifier = Modifier.fillMaxWidth()) {
            Text("Open")
        }
    }
}

private fun mapFeatureDetail(feature: MapFeatureSummary): String =
    listOf(
        feature.featureType,
        feature.grazingPressureRatio?.let { "pressure ${(it * 100).toInt()}%" },
        feature.currentLsu?.let { "LSU $it" },
        feature.waterAlertLevel,
        feature.waterAlertMessage,
    ).filterNotNull().filter { it.isNotBlank() }.joinToString(" | ")

private fun formatHeadCount(value: Double): String =
    if (value % 1.0 == 0.0) value.toInt().toString() else "%.2f".format(value)

private data class MapDrawFeature(
    val source: MapFeatureSummary,
    val polygons: List<List<Offset>>,
    val lines: List<List<Offset>>,
    val points: List<Offset>,
)

private data class MapBounds(val minX: Float, val minY: Float, val maxX: Float, val maxY: Float)

@Composable
private fun OfflineFarmMap(
    features: List<MapFeatureSummary>,
    fullscreen: Boolean,
    modifier: Modifier,
    onSelection: (MapSelection) -> Unit,
) {
    val drawFeatures = remember(features) { features.mapNotNull(::parseMapDrawFeature) }
    val bounds = remember(drawFeatures) { mapBounds(drawFeatures) }
    var zoom by remember { mutableStateOf(1f) }
    var pan by remember { mutableStateOf(Offset.Zero) }
    var canvasSize by remember { mutableStateOf(IntSize.Zero) }
    LaunchedEffect(features, canvasSize) {
        zoom = 1f
        pan = Offset.Zero
    }

    if (drawFeatures.isEmpty() || bounds == null) {
        EntityCard("Map unavailable", "No cached map geometry is available for this farm.")
        return
    }

    Canvas(
        modifier = Modifier
            .fillMaxWidth()
            .then(modifier)
            .onSizeChanged { canvasSize = it }
            .pointerInput(drawFeatures, bounds, canvasSize, zoom, pan) {
                detectTapGestures { tap ->
                    findMapTap(drawFeatures, bounds, canvasSize, zoom, pan, tap)?.let { hit ->
                        onSelection(hit)
                    }
                }
            }
            .pointerInput(Unit) {
                detectTransformGestures { _, gesturePan, gestureZoom, _ ->
                    zoom = (zoom * gestureZoom).coerceIn(0.75f, 8f)
                    pan += gesturePan
                }
            },
    ) {
        drawFeatures.forEach { item ->
            val stroke = Stroke(width = if (item.source.featureType == "water_connection") 4f else 2f)
            item.polygons.forEach { ring ->
                val path = Path()
                ring.forEachIndexed { index, point ->
                    val projected = projectMapPoint(point, bounds, canvasSize, zoom, pan)
                    if (index == 0) path.moveTo(projected.x, projected.y) else path.lineTo(projected.x, projected.y)
                }
                path.close()
                drawPath(
                    path,
                    color = paddockPressureColor(item.source.grazingPressureRatio),
                    alpha = if (item.source.featureType == "paddock") 0.62f else 0.18f,
                )
                drawPath(path, color = Color(0xFF44514D), style = stroke)
            }
            item.lines.forEach { line ->
                val path = Path()
                line.forEachIndexed { index, point ->
                    val projected = projectMapPoint(point, bounds, canvasSize, zoom, pan)
                    if (index == 0) path.moveTo(projected.x, projected.y) else path.lineTo(projected.x, projected.y)
                }
                drawPath(path, color = Color(0xFF2C7FB8), style = stroke)
            }
            item.points.forEach { point ->
                val projected = projectMapPoint(point, bounds, canvasSize, zoom, pan)
                drawCircle(Color(0xFF2563EB), radius = 8f, center = projected)
                drawCircle(Color.White, radius = 3f, center = projected)
            }
            if (item.source.featureType == "paddock" && item.source.mobs.isNotEmpty()) {
                centroid(item.polygons.firstOrNull()).let { center ->
                    item.source.mobs.take(4).forEachIndexed { index, mob ->
                        val projected = projectMapPoint(center + Offset(index * 0.00003f, index * 0.00003f), bounds, canvasSize, zoom, pan)
                        drawCircle(Color(0xFF8A5B2E), radius = 9f, center = projected)
                        drawCircle(Color.White, radius = 4f, center = projected)
                    }
                }
            }
        }
    }
}

private fun parseMapDrawFeature(feature: MapFeatureSummary): MapDrawFeature? {
    val geometry = runCatching { JSONObject(feature.geometryJson) }.getOrNull() ?: return null
    val type = geometry.optString("type")
    val coordinates = geometry.optJSONArray("coordinates") ?: return null
    val polygons = mutableListOf<List<Offset>>()
    val lines = mutableListOf<List<Offset>>()
    val points = mutableListOf<Offset>()
    when (type) {
        "Polygon" -> parsePolygon(coordinates).firstOrNull()?.let { polygons.add(it) }
        "MultiPolygon" -> {
            for (index in 0 until coordinates.length()) {
                parsePolygon(coordinates.getJSONArray(index)).firstOrNull()?.let { polygons.add(it) }
            }
        }
        "LineString" -> lines.add(parseLine(coordinates))
        "MultiLineString" -> {
            for (index in 0 until coordinates.length()) {
                lines.add(parseLine(coordinates.getJSONArray(index)))
            }
        }
        "Point" -> points.add(parsePoint(coordinates))
    }
    if (polygons.isEmpty() && lines.isEmpty() && points.isEmpty()) return null
    return MapDrawFeature(feature, polygons, lines, points)
}

private fun parsePolygon(json: JSONArray): List<List<Offset>> = buildList {
    for (index in 0 until json.length()) {
        add(parseLine(json.getJSONArray(index)))
    }
}

private fun parseLine(json: JSONArray): List<Offset> = buildList {
    for (index in 0 until json.length()) {
        add(parsePoint(json.getJSONArray(index)))
    }
}

private fun parsePoint(json: JSONArray): Offset =
    Offset(json.optDouble(0).toFloat(), json.optDouble(1).toFloat())

private fun mapBounds(features: List<MapDrawFeature>): MapBounds? {
    val paddockPoints = features
        .filter { it.source.featureType == "paddock" && it.polygons.isNotEmpty() }
        .flatMap { it.polygons.flatten() }
    val points = paddockPoints.ifEmpty { features.flatMap { it.polygons.flatten() + it.lines.flatten() + it.points } }
    if (points.isEmpty()) return null
    return MapBounds(
        minX = points.minOf { it.x },
        minY = points.minOf { it.y },
        maxX = points.maxOf { it.x },
        maxY = points.maxOf { it.y },
    )
}

private fun projectMapPoint(point: Offset, bounds: MapBounds, size: IntSize, zoom: Float, pan: Offset): Offset {
    val width = max(1f, bounds.maxX - bounds.minX)
    val height = max(1f, bounds.maxY - bounds.minY)
    val canvasWidth = max(1, size.width).toFloat()
    val canvasHeight = max(1, size.height).toFloat()
    val scale = min(canvasWidth / width, canvasHeight / height) * 0.97f * zoom
    val left = (canvasWidth - width * scale) / 2f
    val top = (canvasHeight - height * scale) / 2f
    return Offset(
        x = left + (point.x - bounds.minX) * scale + pan.x,
        y = top + (bounds.maxY - point.y) * scale + pan.y,
    )
}

private fun findMapTap(
    features: List<MapDrawFeature>,
    bounds: MapBounds,
    size: IntSize,
    zoom: Float,
    pan: Offset,
    tap: Offset,
): MapSelection? {
    val mobHit = features
        .filter { it.source.featureType == "paddock" && it.source.mobs.isNotEmpty() }
        .firstOrNull { item ->
            val center = projectMapPoint(centroid(item.polygons.firstOrNull()), bounds, size, zoom, pan)
            abs(center.x - tap.x) < 28f && abs(center.y - tap.y) < 28f
        }
    if (mobHit != null) return MapSelection(mobHit.source, mobHit.source.mobs.firstOrNull())
    return features
        .filter { it.source.featureType == "paddock" }
        .firstOrNull { item ->
            val center = projectMapPoint(centroid(item.polygons.firstOrNull()), bounds, size, zoom, pan)
            abs(center.x - tap.x) < 60f && abs(center.y - tap.y) < 60f
        }?.let { MapSelection(it.source) }
}

private fun centroid(points: List<Offset>?): Offset {
    if (points.isNullOrEmpty()) return Offset.Zero
    return Offset(points.sumOf { it.x.toDouble() }.toFloat() / points.size, points.sumOf { it.y.toDouble() }.toFloat() / points.size)
}

private fun paddockPressureColor(ratio: Double?): Color {
    if (ratio == null) return Color(0xFF8F9A96)
    val clamped = ratio.coerceIn(0.0, 1.0)
    return when {
        clamped < 0.2 -> Color(0xFF38761D)
        clamped < 0.4 -> Color(0xFF6AA84F)
        clamped < 0.6 -> Color(0xFFB6D7A8)
        clamped < 0.8 -> Color(0xFFF6B26B)
        else -> Color(0xFFCC0000)
    }
}

@Composable
private fun CalendarScreen(
    state: FieldUiState,
    onBackHome: () -> Unit,
    onOpenScreen: (AppScreen) -> Unit,
    onTaskSelected: (String) -> Unit,
    onCalendarItemSelected: (CalendarItemSummary) -> Unit,
    onQueueTaskStatus: (TaskSummary, String) -> Unit,
    onTaskCommentChange: (String) -> Unit,
    onQueueTaskComment: (TaskSummary) -> Unit,
    onAttachTaskPhoto: (TaskSummary) -> Unit,
) {
    FormScaffold("Calendar", onBackHome) {
        val snapshot = state.snapshot ?: return@FormScaffold
        var filters by remember(snapshot.farm.id) { mutableStateOf(CalendarFilterState(startDate = LocalDate.now().toString())) }
        val filteredItems = filterCalendarItems(snapshot.calendarItems, filters)

        Button(onClick = { onOpenScreen(AppScreen.TaskCreate) }, modifier = Modifier.fillMaxWidth()) {
            Text("New Task")
        }
        CalendarFilters(
            filters = filters,
            snapshot = snapshot,
            onFiltersChange = { filters = it },
        )
        if (filteredItems.isEmpty()) {
            EntityCard("No calendar items", "No tasks or activities match the current filters.")
        }
        filteredItems.take(80).forEach { item ->
            val task = item.taskId?.let { id -> snapshot.tasks.firstOrNull { it.id == id } }
            CalendarItemCard(
                item = item,
                task = task,
                state = state,
                onCalendarItemSelected = onCalendarItemSelected,
                onTaskSelected = onTaskSelected,
                onQueueTaskStatus = onQueueTaskStatus,
                onTaskCommentChange = onTaskCommentChange,
                onQueueTaskComment = onQueueTaskComment,
                onAttachTaskPhoto = onAttachTaskPhoto,
            )
        }
    }
}

@Composable
private fun CalendarFilters(
    filters: CalendarFilterState,
    snapshot: FarmSnapshot,
    onFiltersChange: (CalendarFilterState) -> Unit,
) {
    SectionCard("Filters") {
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp), modifier = Modifier.fillMaxWidth()) {
            OutlinedTextField(
                value = filters.startDate,
                onValueChange = { onFiltersChange(filters.copy(startDate = it)) },
                label = { Text("From") },
                singleLine = true,
                modifier = Modifier.weight(1f),
            )
            OutlinedTextField(
                value = filters.endDate,
                onValueChange = { onFiltersChange(filters.copy(endDate = it)) },
                label = { Text("To") },
                singleLine = true,
                modifier = Modifier.weight(1f),
            )
        }
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp), modifier = Modifier.fillMaxWidth()) {
            FilterChoice("All", filters.itemType.isBlank(), Modifier.weight(1f)) {
                onFiltersChange(filters.copy(itemType = ""))
            }
            FilterChoice("Tasks", filters.itemType == "task", Modifier.weight(1f)) {
                onFiltersChange(filters.copy(itemType = "task"))
            }
            FilterChoice("Activity", filters.itemType == "activity", Modifier.weight(1f)) {
                onFiltersChange(filters.copy(itemType = "activity"))
            }
        }
        OutlinedTextField(
            value = filters.name,
            onValueChange = { onFiltersChange(filters.copy(name = it)) },
            label = { Text("Name") },
            modifier = Modifier.fillMaxWidth(),
        )
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp), modifier = Modifier.fillMaxWidth()) {
            OutlinedTextField(
                value = filters.stage,
                onValueChange = { onFiltersChange(filters.copy(stage = it)) },
                label = { Text("Stage") },
                singleLine = true,
                modifier = Modifier.weight(1f),
            )
            OutlinedTextField(
                value = filters.assignee,
                onValueChange = { onFiltersChange(filters.copy(assignee = it)) },
                label = { Text("Assignee") },
                singleLine = true,
                modifier = Modifier.weight(1f),
            )
        }
        OutlinedTextField(
            value = filters.tag,
            onValueChange = { onFiltersChange(filters.copy(tag = it)) },
            label = { Text("Tag") },
            singleLine = true,
            modifier = Modifier.fillMaxWidth(),
        )
        EntityFilterPickers(snapshot, filters, onFiltersChange)
    }
}

@Composable
private fun EntityFilterPickers(
    snapshot: FarmSnapshot,
    filters: CalendarFilterState,
    onFiltersChange: (CalendarFilterState) -> Unit,
) {
    PaddockFilterPicker(
        paddocks = snapshot.paddocks,
        selectedPaddockId = filters.paddockId,
        onPaddockSelected = { onFiltersChange(filters.copy(paddockId = it)) },
    )
    WaterAssetFilterPicker(
        assets = snapshot.waterAssets,
        selectedWaterAssetId = filters.waterAssetId,
        onWaterAssetSelected = { onFiltersChange(filters.copy(waterAssetId = it)) },
    )
    MobFilterPicker(
        mobs = snapshot.mobs,
        selectedMobId = filters.mobId,
        onMobSelected = { onFiltersChange(filters.copy(mobId = it)) },
    )
    OutlinedButton(
        onClick = { onFiltersChange(filters.copy(paddockId = "", waterAssetId = "", mobId = "")) },
        modifier = Modifier.fillMaxWidth(),
    ) {
        Text("Clear Entity Filters")
    }
}

@Composable
private fun CalendarItemCard(
    item: CalendarItemSummary,
    task: TaskSummary?,
    state: FieldUiState,
    onCalendarItemSelected: (CalendarItemSummary) -> Unit,
    onTaskSelected: (String) -> Unit,
    onQueueTaskStatus: (TaskSummary, String) -> Unit,
    onTaskCommentChange: (String) -> Unit,
    onQueueTaskComment: (TaskSummary) -> Unit,
    onAttachTaskPhoto: (TaskSummary) -> Unit,
) {
    if (task != null) {
        TaskCard(
            state = state,
            task = task,
            onTaskSelected = onTaskSelected,
            onQueueTaskStatus = onQueueTaskStatus,
            onTaskCommentChange = onTaskCommentChange,
            onQueueTaskComment = onQueueTaskComment,
            onAttachTaskPhoto = onAttachTaskPhoto,
        )
        return
    }
    Surface(
        color = MaterialTheme.colorScheme.surface,
        shape = RoundedCornerShape(8.dp),
        border = BorderStroke(1.dp, MaterialTheme.colorScheme.outline),
        modifier = Modifier
            .fillMaxWidth()
            .clickable { onCalendarItemSelected(item) },
    ) {
        Column(modifier = Modifier.padding(10.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
            Text(item.title, fontWeight = FontWeight.SemiBold)
            Text("${item.date} | ${item.stageLabel ?: item.badgeText ?: item.kind}", color = Color(0xFF516052))
            item.subtitle?.let { Text(it, style = MaterialTheme.typography.bodySmall, color = Color(0xFF516052)) }
        }
    }
}

@Composable
private fun CalendarItemDetailScreen(state: FieldUiState, onBackHome: () -> Unit) {
    FormScaffold("Calendar Detail", onBackHome) {
        val item = state.snapshot?.calendarItems?.firstOrNull {
            it.sourceId == state.selectedCalendarItemSourceId && it.date == state.selectedCalendarItemDate
        } ?: state.snapshot?.calendarItems?.firstOrNull() ?: return@FormScaffold
        EntityCard(item.title, "${item.date} | ${item.stageLabel ?: item.badgeText ?: item.kind}")
        item.description?.takeIf { it.isNotBlank() }?.let {
            Text(it, style = MaterialTheme.typography.bodyMedium)
        }
        MetricRows(
            listOf(
                "Type" to item.kind.replaceFirstChar(Char::titlecase),
                "Duration" to (item.durationText ?: "-"),
                "Recurrence" to (item.recurrenceText ?: "-"),
            )
        )
    }
}

@Composable
private fun TasksScreen(
    state: FieldUiState,
    onBackHome: () -> Unit,
    onOpenScreen: (AppScreen) -> Unit,
    onTaskSelected: (String) -> Unit,
    onQueueTaskStatus: (TaskSummary, String) -> Unit,
    onTaskCommentChange: (String) -> Unit,
    onQueueTaskComment: (TaskSummary) -> Unit,
    onAttachTaskPhoto: (TaskSummary) -> Unit,
) {
    FormScaffold("Tasks", onBackHome) {
        Button(onClick = { onOpenScreen(AppScreen.TaskCreate) }, modifier = Modifier.fillMaxWidth()) {
            Text("New Task")
        }
        val tasks = state.snapshot?.tasks.orEmpty()
        if (tasks.isEmpty()) {
            EntityCard("No open tasks", "Create a task from the field when work needs to be captured.")
        }
        tasks.forEach { task ->
            TaskCard(
                state = state,
                task = task,
                onTaskSelected = onTaskSelected,
                onQueueTaskStatus = onQueueTaskStatus,
                onTaskCommentChange = onTaskCommentChange,
                onQueueTaskComment = onQueueTaskComment,
                onAttachTaskPhoto = onAttachTaskPhoto,
            )
        }
    }
}

@Composable
private fun TaskDetailScreen(
    state: FieldUiState,
    onBackHome: () -> Unit,
    onQueueTaskStatus: (TaskSummary, String) -> Unit,
    onTaskCommentChange: (String) -> Unit,
    onQueueTaskComment: (TaskSummary) -> Unit,
    onAttachTaskPhoto: (TaskSummary) -> Unit,
) {
    FormScaffold("Task Detail", onBackHome) {
        val task = state.snapshot?.tasks?.firstOrNull { it.id == state.selectedTaskId }
            ?: state.snapshot?.tasks?.firstOrNull()
            ?: return@FormScaffold
        TaskCard(
            state = state,
            task = task,
            onTaskSelected = {},
            onQueueTaskStatus = onQueueTaskStatus,
            onTaskCommentChange = onTaskCommentChange,
            onQueueTaskComment = onQueueTaskComment,
            onAttachTaskPhoto = onAttachTaskPhoto,
        )
        Text(task.description, style = MaterialTheme.typography.bodyMedium)
        task.entityLinks.forEach { link ->
            EntityCard(link.entityName ?: link.entityType, link.entityType)
        }
        task.attachments.forEach { attachment ->
            EntityCard(attachment.originalFilename, attachment.caption ?: "${attachment.contentType} | ${attachment.byteSize} bytes")
        }
    }
}

@Composable
private fun TaskCard(
    state: FieldUiState,
    task: TaskSummary,
    onTaskSelected: (String) -> Unit,
    onQueueTaskStatus: (TaskSummary, String) -> Unit,
    onTaskCommentChange: (String) -> Unit,
    onQueueTaskComment: (TaskSummary) -> Unit,
    onAttachTaskPhoto: (TaskSummary) -> Unit,
) {
    Surface(
        color = MaterialTheme.colorScheme.surface,
        shape = RoundedCornerShape(8.dp),
        border = BorderStroke(1.dp, MaterialTheme.colorScheme.outline),
        modifier = Modifier
            .fillMaxWidth()
            .clickable { onTaskSelected(task.id) },
    ) {
        Column(modifier = Modifier.padding(10.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Text("${task.displayKey} ${task.heading}", fontWeight = FontWeight.SemiBold)
            Text("${task.statusLabel} | ${task.priorityLabel} | ${task.dueDate ?: "no due date"}", color = Color(0xFF516052))
            task.assigneeName?.let { Text("Assignee: $it", style = MaterialTheme.typography.bodySmall, color = Color(0xFF516052)) }
            if (task.tags.isNotEmpty()) {
                Text("Tags: ${task.tags.joinToString(", ")}", style = MaterialTheme.typography.bodySmall, color = Color(0xFF516052))
            }
            if (task.attachmentCount > 0) {
                Text("${task.attachmentCount} photo(s)", style = MaterialTheme.typography.bodySmall, color = Color(0xFF516052))
            }
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp), modifier = Modifier.fillMaxWidth()) {
                OutlinedButton(onClick = { onQueueTaskStatus(task, "in_progress") }, modifier = Modifier.weight(1f)) {
                    Text("Start")
                }
                OutlinedButton(onClick = { onQueueTaskStatus(task, "closed") }, modifier = Modifier.weight(1f)) {
                    Text("Close")
                }
            }
            OutlinedButton(onClick = { onAttachTaskPhoto(task) }, modifier = Modifier.fillMaxWidth()) {
                Text("Add Image")
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

@Composable
private fun DecisionsScreen(
    state: FieldUiState,
    onBackHome: () -> Unit,
    onOpenScreen: (AppScreen) -> Unit,
) {
    FormScaffold("Decisions", onBackHome) {
        val decisions = state.snapshot?.decisionFeed.orEmpty()
        if (decisions.isEmpty()) {
            EntityCard("No decisions", "No urgent field decisions in the cached snapshot.")
        }
        decisions.forEach { item ->
            Surface(
                color = if (item.severity == "high") Color(0xFFF8EAE4) else Color(0xFFFFF6DF),
                shape = RoundedCornerShape(8.dp),
                border = BorderStroke(1.dp, MaterialTheme.colorScheme.outline),
                modifier = Modifier.fillMaxWidth(),
            ) {
                Column(modifier = Modifier.padding(10.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    Text(item.title, fontWeight = FontWeight.SemiBold)
                    Text(item.detail, color = Color(0xFF516052))
                    val target = when (item.entityType) {
                        "paddock" -> AppScreen.Paddocks
                        "mob" -> AppScreen.Mobs
                        "water_asset" -> AppScreen.WaterAssets
                        "task" -> AppScreen.Calendar
                        else -> AppScreen.Calendar
                    }
                    OutlinedButton(onClick = { onOpenScreen(target) }, modifier = Modifier.fillMaxWidth()) {
                        Text("Open")
                    }
                }
            }
        }
    }
}

@Composable
private fun MobFilters(mobs: List<MobSummary>, filters: MobFilterState, onFiltersChange: (MobFilterState) -> Unit) {
    val species = mobs.flatMap { mob -> mob.balances.map { it.animalGroupType.species } }
        .filter { it.isNotBlank() }
        .distinct()
        .sorted()
    SectionCard("Filters") {
        OutlinedTextField(
            value = filters.name,
            onValueChange = { onFiltersChange(filters.copy(name = it)) },
            label = { Text("Name") },
            modifier = Modifier.fillMaxWidth(),
        )
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp), modifier = Modifier.fillMaxWidth()) {
            OutlinedTextField(
                value = filters.countMin,
                onValueChange = { onFiltersChange(filters.copy(countMin = it)) },
                label = { Text("Min count") },
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
                singleLine = true,
                modifier = Modifier.weight(1f),
            )
            OutlinedTextField(
                value = filters.countMax,
                onValueChange = { onFiltersChange(filters.copy(countMax = it)) },
                label = { Text("Max count") },
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
                singleLine = true,
                modifier = Modifier.weight(1f),
            )
        }
        if (species.isNotEmpty()) {
            FieldLabel("Species")
            species.chunked(2).forEach { row ->
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp), modifier = Modifier.fillMaxWidth()) {
                    row.forEach { value ->
                        val selected = value in filters.species
                        FilterChoice(value, selected, Modifier.weight(1f)) {
                            onFiltersChange(
                                filters.copy(
                                    species = if (selected) filters.species - value else filters.species + value,
                                )
                            )
                        }
                    }
                    if (row.size == 1) {
                        Spacer(modifier = Modifier.weight(1f))
                    }
                }
            }
        }
    }
}

@Composable
private fun PaddockFilters(filters: PaddockFilterState, onFiltersChange: (PaddockFilterState) -> Unit) {
    SectionCard("Filters") {
        OutlinedTextField(
            value = filters.name,
            onValueChange = { onFiltersChange(filters.copy(name = it)) },
            label = { Text("Name") },
            modifier = Modifier.fillMaxWidth(),
        )
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp), modifier = Modifier.fillMaxWidth()) {
            OutlinedTextField(
                value = filters.stockMin,
                onValueChange = { onFiltersChange(filters.copy(stockMin = it)) },
                label = { Text("Min stock") },
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
                singleLine = true,
                modifier = Modifier.weight(1f),
            )
            OutlinedTextField(
                value = filters.stockMax,
                onValueChange = { onFiltersChange(filters.copy(stockMax = it)) },
                label = { Text("Max stock") },
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
                singleLine = true,
                modifier = Modifier.weight(1f),
            )
        }
        OutlinedTextField(
            value = filters.tag,
            onValueChange = { onFiltersChange(filters.copy(tag = it)) },
            label = { Text("Tag") },
            singleLine = true,
            modifier = Modifier.fillMaxWidth(),
        )
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp), modifier = Modifier.fillMaxWidth()) {
            OutlinedTextField(
                value = filters.pressureMin,
                onValueChange = { onFiltersChange(filters.copy(pressureMin = it)) },
                label = { Text("Min pressure %") },
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
                singleLine = true,
                modifier = Modifier.weight(1f),
            )
            OutlinedTextField(
                value = filters.pressureMax,
                onValueChange = { onFiltersChange(filters.copy(pressureMax = it)) },
                label = { Text("Max pressure %") },
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
                singleLine = true,
                modifier = Modifier.weight(1f),
            )
        }
    }
}

@Composable
private fun WaterFilters(
    assets: List<WaterAssetSummary>,
    filters: WaterFilterState,
    onFiltersChange: (WaterFilterState) -> Unit,
) {
    SectionCard("Filters") {
        OutlinedTextField(
            value = filters.name,
            onValueChange = { onFiltersChange(filters.copy(name = it)) },
            label = { Text("Name") },
            modifier = Modifier.fillMaxWidth(),
        )
        SimpleValuePicker(
            label = "Type",
            selectedValue = filters.assetType,
            values = assets.map { it.assetType }.filter { it.isNotBlank() }.distinct().sorted(),
            allLabel = "All types",
            onSelected = { onFiltersChange(filters.copy(assetType = it)) },
        )
        SimpleValuePicker(
            label = "Status",
            selectedValue = filters.status,
            values = (assets.mapNotNull { it.status }.filter { it.isNotBlank() } + "unknown").distinct().sorted(),
            allLabel = "All statuses",
            onSelected = { onFiltersChange(filters.copy(status = it)) },
        )
        SimpleValuePicker(
            label = "Water level",
            selectedValue = filters.waterLevel,
            values = (assets.mapNotNull { it.waterLevel }.filter { it.isNotBlank() } + "unknown").distinct().sorted(),
            allLabel = "All levels",
            onSelected = { onFiltersChange(filters.copy(waterLevel = it)) },
        )
    }
}

@Composable
private fun MobsScreen(
    state: FieldUiState,
    onBackHome: () -> Unit,
    onMobSelected: (String) -> Unit,
    onOpenScreen: (AppScreen) -> Unit,
) {
    FormScaffold("Mobs", onBackHome) {
        val snapshot = state.snapshot ?: return@FormScaffold
        var filters by remember(snapshot.farm.id) { mutableStateOf(MobFilterState()) }
        val filteredMobs = filterMobs(snapshot.mobs, filters)
        MobFilters(snapshot.mobs, filters) { filters = it }
        if (filteredMobs.isEmpty()) {
            EntityCard("No mobs", "No mobs match the current filters.")
        }
        filteredMobs.forEach { mob ->
            ClickableEntityCard(
                title = mob.name,
                detail = "${mob.totalHead} head | ${mob.status}",
            ) {
                onMobSelected(mob.id)
                onOpenScreen(AppScreen.MobDetail)
            }
        }
    }
}

@Composable
private fun MobDetailScreen(
    state: FieldUiState,
    onBackToList: () -> Unit,
    onMobSelected: (String) -> Unit,
    onPaddockSelected: (String) -> Unit,
    onOpenScreen: (AppScreen) -> Unit,
    onStartTaskForEntity: (String, String, String) -> Unit,
) {
    FormScaffold("Mob Detail", onBackToList) {
        val snapshot = state.snapshot ?: return@FormScaffold
        val mob = selectedMob(state) ?: return@FormScaffold
        EntityCard(mob.name, "${mob.status} | ${mob.totalHead} head")
        MetricRows(
            listOf(
                "Status" to mob.status,
                "Total head" to mob.totalHead.toString(),
            )
        )
        SectionCard("Balances") {
            if (mob.balances.isEmpty()) {
                Text("No stock balances recorded", color = Color(0xFF516052))
            }
            mob.balances.forEach { balance ->
                EntityCard(balance.animalGroupType.label, "${balance.headCount} head")
            }
        }
        SectionCard("Grazing Paddocks") {
            val paddocks = mobGrazingPaddocks(snapshot, mob.id)
            if (paddocks.isEmpty()) {
                Text("No active paddock allocations", color = Color(0xFF516052))
            }
            paddocks.forEach { paddock ->
                OutlinedButton(
                    onClick = {
                        onPaddockSelected(paddock.paddockId)
                        onOpenScreen(AppScreen.PaddockDetail)
                    },
                    modifier = Modifier.fillMaxWidth(),
                ) {
                    Column(modifier = Modifier.fillMaxWidth(), verticalArrangement = Arrangement.spacedBy(2.dp)) {
                        Text(paddock.paddockName, fontWeight = FontWeight.SemiBold)
                        Text("${formatHeadCount(paddock.allocationPct)}% allocation", style = MaterialTheme.typography.bodySmall)
                    }
                }
            }
        }
        SectionCard("Linked Tasks") {
            RelatedTasks(snapshot.tasks, "mob", mob.id)
        }
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp), modifier = Modifier.fillMaxWidth()) {
            OutlinedButton(
                onClick = { onMobSelected(mob.id); onOpenScreen(AppScreen.StockCount) },
                modifier = Modifier.weight(1f),
            ) {
                Text("Adjust Counts")
            }
            OutlinedButton(
                onClick = { onMobSelected(mob.id); onOpenScreen(AppScreen.MoveMob) },
                modifier = Modifier.weight(1f),
            ) {
                Text("Move Mob")
            }
        }
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp), modifier = Modifier.fillMaxWidth()) {
            OutlinedButton(
                onClick = { onMobSelected(mob.id); onOpenScreen(AppScreen.TransferMob) },
                modifier = Modifier.weight(1f),
            ) {
                Text("Transfer Stock")
            }
            OutlinedButton(
                onClick = { onStartTaskForEntity("mob", mob.id, "Check ${mob.name}") },
                modifier = Modifier.weight(1f),
            ) {
                Text("Create Linked Task")
            }
        }
    }
}

@Composable
private fun PaddocksScreen(
    state: FieldUiState,
    onBackHome: () -> Unit,
    onPaddockSelected: (String) -> Unit,
    onOpenScreen: (AppScreen) -> Unit,
) {
    FormScaffold("Paddocks", onBackHome) {
        val snapshot = state.snapshot ?: return@FormScaffold
        var filters by remember(snapshot.farm.id) { mutableStateOf(PaddockFilterState()) }
        val filteredPaddocks = filterPaddocks(snapshot, filters)
        PaddockFilters(filters) { filters = it }
        if (filteredPaddocks.isEmpty()) {
            EntityCard("No paddocks", "No paddocks match the current filters.")
        }
        filteredPaddocks.forEach { paddock ->
            val grazing = snapshot.grazingByPaddock.firstOrNull { it.paddockId == paddock.id }
            val water = snapshot.waterAssets.filter { it.locationPaddockId == paddock.id || paddock.id in it.servedPaddockIds }
            ClickableEntityCard(
                title = "${paddock.name} | ${paddock.status}",
                detail = "Expected stock: ${grazing?.totalHead ?: 0.0}; Water links: ${water.size}; Tags: ${paddock.tagLabel.ifBlank { "none" }}",
            ) {
                onPaddockSelected(paddock.id)
                onOpenScreen(AppScreen.PaddockDetail)
            }
        }
    }
}

@Composable
private fun PaddockDetailScreen(
    state: FieldUiState,
    onBackToList: () -> Unit,
    onPaddockSelected: (String) -> Unit,
    onMobSelected: (String) -> Unit,
    onOpenScreen: (AppScreen) -> Unit,
    onStartTaskForEntity: (String, String, String) -> Unit,
) {
    FormScaffold("Paddock Detail", onBackToList) {
        val snapshot = state.snapshot ?: return@FormScaffold
        val paddock = selectedPaddock(state) ?: return@FormScaffold
        val grazing = paddockGrazing(snapshot, paddock.id)
        val feature = paddockMapFeature(snapshot, paddock.id)
        val water = paddockWaterAvailability(snapshot, paddock.id)
        EntityCard(
            title = "${paddock.name} | ${paddock.status}",
            detail = "Expected stock: ${grazing?.totalHead ?: 0.0}; Water links: ${water.assets.size}; Tags: ${paddock.tagLabel.ifBlank { "none" }}",
        )
        paddock.notes?.takeIf { it.isNotBlank() }?.let { Text(it, color = Color(0xFF516052)) }
        SectionCard("Pressure") {
            MetricRows(
                listOf(
                    "Pressure" to (feature?.grazingPressureRatio?.let { "${"%.0f".format(it * 100.0)}%" } ?: "unknown"),
                    "Current LSU" to (feature?.currentLsu?.let(::formatHeadCount) ?: "-"),
                    "Ha/current LSU" to (feature?.hectaresPerCurrentLsu?.let { "%.2f".format(it) } ?: "-"),
                )
            )
        }
        SectionCard("Water Availability") {
            water.alertMessage?.takeIf { it.isNotBlank() }?.let { message ->
                Text("${water.alertLevel ?: "alert"}: $message", color = Color(0xFF7A3424))
            }
            if (water.assets.isEmpty()) {
                Text("No linked water assets", color = Color(0xFF516052))
            }
            water.assets.forEach { asset ->
                EntityCard(
                    title = asset.name,
                    detail = "${asset.assetTypeLabel} | ${asset.status ?: "unknown"} | level ${asset.waterLevel ?: "unknown"}",
                )
            }
        }
        SectionCard("Stock In Paddock") {
            val stockLines = paddockStockLines(grazing)
            if (stockLines.isEmpty()) {
                Text("No stock recorded in this paddock", color = Color(0xFF516052))
            }
            stockLines.forEach { line ->
                Text("${formatHeadCount(line.head)} ${line.label}", style = MaterialTheme.typography.bodySmall)
            }
            val grazingMobs = grazing?.mobs.orEmpty()
            Text("Mobs grazing", fontWeight = FontWeight.SemiBold)
            if (grazingMobs.isEmpty()) {
                Text("No active mob allocation links", color = Color(0xFF516052))
            }
            grazingMobs.forEach { mob ->
                OutlinedButton(
                    onClick = {
                        onMobSelected(mob.mobId)
                        onOpenScreen(AppScreen.MobDetail)
                    },
                    modifier = Modifier.fillMaxWidth(),
                ) {
                    Column(modifier = Modifier.fillMaxWidth(), verticalArrangement = Arrangement.spacedBy(2.dp)) {
                        Text(mob.mobName, fontWeight = FontWeight.SemiBold)
                        Text("${formatHeadCount(mob.allocationPct)}% allocation", style = MaterialTheme.typography.bodySmall)
                    }
                }
            }
        }
        SectionCard("Linked Tasks") {
            RelatedTasks(snapshot.tasks, "paddock", paddock.id)
        }
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp), modifier = Modifier.fillMaxWidth()) {
            OutlinedButton(
                onClick = { onPaddockSelected(paddock.id); onOpenScreen(AppScreen.PaddockEdit) },
                modifier = Modifier.weight(1f),
            ) {
                Text("Edit Paddock")
            }
            OutlinedButton(
                onClick = { onStartTaskForEntity("paddock", paddock.id, "Inspect ${paddock.name}") },
                modifier = Modifier.weight(1f),
            ) {
                Text("Create Linked Task")
            }
        }
    }
}

@Composable
private fun WaterAssetsScreen(
    state: FieldUiState,
    onBackHome: () -> Unit,
    onWaterAssetSelected: (String) -> Unit,
    onOpenScreen: (AppScreen) -> Unit,
) {
    FormScaffold("Water Assets", onBackHome) {
        val snapshot = state.snapshot ?: return@FormScaffold
        var filters by remember(snapshot.farm.id) { mutableStateOf(WaterFilterState()) }
        val filteredAssets = filterWaterAssets(snapshot.waterAssets, filters)
        WaterFilters(snapshot.waterAssets, filters) { filters = it }
        if (filteredAssets.isEmpty()) {
            EntityCard("No water assets", "No water assets match the current filters.")
        }
        filteredAssets.forEach { asset ->
            ClickableEntityCard(
                title = asset.name,
                detail = "${asset.assetTypeLabel} | ${asset.status ?: "unknown"} | level ${asset.waterLevel ?: "unknown"}",
            ) {
                onWaterAssetSelected(asset.id)
                onOpenScreen(AppScreen.WaterAssetDetail)
            }
        }
    }
}

@Composable
private fun WaterAssetDetailScreen(
    state: FieldUiState,
    onBackToList: () -> Unit,
    onWaterAssetSelected: (String) -> Unit,
    onOpenScreen: (AppScreen) -> Unit,
    onStartTaskForEntity: (String, String, String) -> Unit,
) {
    FormScaffold("Water Asset Detail", onBackToList) {
        val snapshot = state.snapshot ?: return@FormScaffold
        val asset = selectedWaterAsset(state) ?: return@FormScaffold
        EntityCard(asset.name, "${asset.assetTypeLabel} | ${asset.status ?: "unknown"} | level ${asset.waterLevel ?: "unknown"}")
        MetricRows(
            listOf(
                "Type" to asset.assetTypeLabel,
                "Active" to if (asset.active) "Yes" else "No",
                "Status" to (asset.status ?: "unknown"),
                "Water level" to (asset.waterLevel ?: "unknown"),
                "Location" to (asset.locationPaddockName ?: asset.locationPaddockId ?: "Unlocated"),
            )
        )
        SectionCard("Served Paddocks") {
            val servedNames = servedPaddockNames(snapshot, asset)
            if (servedNames.isEmpty()) {
                Text("No served paddocks listed", color = Color(0xFF516052))
            }
            servedNames.forEach { Text(it, style = MaterialTheme.typography.bodySmall) }
        }
        asset.networkWarning?.takeIf { it.isNotBlank() }?.let { warning ->
            SectionCard("Network Warning") {
                Text(warning, color = Color(0xFF7A3424))
            }
        }
        SectionCard("Linked Tasks") {
            RelatedTasks(snapshot.tasks, "water_asset", asset.id)
        }
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp), modifier = Modifier.fillMaxWidth()) {
            OutlinedButton(
                onClick = { onWaterAssetSelected(asset.id); onOpenScreen(AppScreen.WaterEdit) },
                modifier = Modifier.weight(1f),
            ) {
                Text("Update Water")
            }
            OutlinedButton(
                onClick = { onStartTaskForEntity("water_asset", asset.id, "Check ${asset.name}") },
                modifier = Modifier.weight(1f),
            ) {
                Text("Create Linked Task")
            }
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
        val asset = selectedWaterAsset(state)
        Text(asset?.name ?: "Water asset", fontWeight = FontWeight.SemiBold)
        Row(verticalAlignment = Alignment.CenterVertically) {
            Checkbox(checked = state.waterActive, onCheckedChange = onWaterActiveChange)
            Text("Active")
        }
        val statusOptions = state.formOptions.waterStatusOptionsByType[asset?.assetType.orEmpty()].orEmpty()
        OptionPicker("Status", state.waterStatus, statusOptions, onWaterStatusChange)
        if (asset?.assetType in state.formOptions.waterLevelAssetTypes) {
            OptionPicker("Water level", state.waterLevel, state.formOptions.waterLevelOptions, onWaterLevelChange)
        }
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
    onAttachNewTaskPhoto: () -> Unit,
) {
    FormScaffold("New Task", onBackHome) {
        Text("Linked to ${state.taskEntityType}", color = Color(0xFF516052))
        OutlinedTextField(state.taskHeading, onTaskHeadingChange, label = { Text("Heading") }, modifier = Modifier.fillMaxWidth())
        OutlinedTextField(state.taskDescription, onTaskDescriptionChange, label = { Text("Description") }, modifier = Modifier.fillMaxWidth())
        OutlinedTextField(state.taskDueDate, onTaskDueDateChange, label = { Text("Due date") }, modifier = Modifier.fillMaxWidth())
        Button(onClick = onQueueTaskCreate, enabled = state.taskHeading.isNotBlank(), modifier = Modifier.fillMaxWidth()) {
            Text("Queue Task")
        }
        OutlinedButton(onClick = onAttachNewTaskPhoto, enabled = state.taskHeading.isNotBlank(), modifier = Modifier.fillMaxWidth()) {
            Text("Add Image")
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
private fun PaddockFilterPicker(
    paddocks: List<PaddockSummary>,
    selectedPaddockId: String,
    onPaddockSelected: (String) -> Unit,
) {
    var expanded by remember { mutableStateOf(false) }
    val selected = paddocks.firstOrNull { it.id == selectedPaddockId }
    Picker("Paddock filter", selected?.name ?: "All paddocks", paddocks.isNotEmpty(), expanded, { expanded = it }) {
        DropdownMenuItem(text = { Text("All paddocks") }, onClick = { expanded = false; onPaddockSelected("") })
        paddocks.forEach { paddock ->
            DropdownMenuItem(text = { Text(paddock.name) }, onClick = { expanded = false; onPaddockSelected(paddock.id) })
        }
    }
}

@Composable
private fun MobFilterPicker(
    mobs: List<MobSummary>,
    selectedMobId: String,
    onMobSelected: (String) -> Unit,
) {
    var expanded by remember { mutableStateOf(false) }
    val selected = mobs.firstOrNull { it.id == selectedMobId }
    Picker("Mob filter", selected?.name ?: "All mobs", mobs.isNotEmpty(), expanded, { expanded = it }) {
        DropdownMenuItem(text = { Text("All mobs") }, onClick = { expanded = false; onMobSelected("") })
        mobs.forEach { mob ->
            DropdownMenuItem(text = { Text(mob.name) }, onClick = { expanded = false; onMobSelected(mob.id) })
        }
    }
}

@Composable
private fun WaterAssetFilterPicker(
    assets: List<WaterAssetSummary>,
    selectedWaterAssetId: String,
    onWaterAssetSelected: (String) -> Unit,
) {
    var expanded by remember { mutableStateOf(false) }
    val selected = assets.firstOrNull { it.id == selectedWaterAssetId }
    Picker("Water filter", selected?.name ?: "All water assets", assets.isNotEmpty(), expanded, { expanded = it }) {
        DropdownMenuItem(text = { Text("All water assets") }, onClick = { expanded = false; onWaterAssetSelected("") })
        assets.forEach { asset ->
            DropdownMenuItem(text = { Text(asset.name) }, onClick = { expanded = false; onWaterAssetSelected(asset.id) })
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
private fun OptionPicker(
    label: String,
    selectedValue: String,
    options: List<MobileOption>,
    onSelected: (String) -> Unit,
) {
    var expanded by remember { mutableStateOf(false) }
    val selected = options.firstOrNull { it.value == selectedValue } ?: options.firstOrNull()
    Picker(label, selected?.label ?: "Not applicable", options.isNotEmpty(), expanded, { expanded = it }) {
        options.forEach { option ->
            DropdownMenuItem(text = { Text(option.label) }, onClick = { expanded = false; onSelected(option.value) })
        }
    }
}

@Composable
private fun SimpleValuePicker(
    label: String,
    selectedValue: String,
    values: List<String>,
    allLabel: String,
    onSelected: (String) -> Unit,
) {
    var expanded by remember { mutableStateOf(false) }
    Picker(label, selectedValue.ifBlank { allLabel }, values.isNotEmpty(), expanded, { expanded = it }) {
        DropdownMenuItem(text = { Text(allLabel) }, onClick = { expanded = false; onSelected("") })
        values.forEach { value ->
            DropdownMenuItem(text = { Text(value.replace("_", " ").replaceFirstChar(Char::titlecase)) }, onClick = { expanded = false; onSelected(value) })
        }
    }
}

@Composable
private fun FilterChoice(label: String, selected: Boolean, modifier: Modifier = Modifier, onClick: () -> Unit) {
    if (selected) {
        Button(onClick = onClick, modifier = modifier) { Text(label) }
    } else {
        OutlinedButton(onClick = onClick, modifier = modifier) { Text(label) }
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
private fun ClickableEntityCard(title: String, detail: String, onClick: () -> Unit) {
    Surface(
        color = MaterialTheme.colorScheme.surface,
        shape = RoundedCornerShape(8.dp),
        border = BorderStroke(1.dp, MaterialTheme.colorScheme.outline),
        modifier = Modifier
            .fillMaxWidth()
            .clickable(onClick = onClick),
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

private data class MetricAction(val label: String, val value: String, val screen: AppScreen)

@Composable
private fun MetricActionRows(metrics: List<MetricAction>, onOpenScreen: (AppScreen) -> Unit) {
    metrics.chunked(2).forEach { row ->
        Row(horizontalArrangement = Arrangement.spacedBy(10.dp), modifier = Modifier.fillMaxWidth()) {
            row.forEach { action ->
                Surface(
                    modifier = Modifier
                        .weight(1f)
                        .clickable { onOpenScreen(action.screen) },
                    color = MaterialTheme.colorScheme.surfaceVariant,
                    shape = RoundedCornerShape(8.dp),
                    border = BorderStroke(1.dp, MaterialTheme.colorScheme.outline),
                ) {
                    Column(modifier = Modifier.padding(10.dp), verticalArrangement = Arrangement.spacedBy(3.dp)) {
                        Text(action.label, style = MaterialTheme.typography.labelMedium, color = Color(0xFF516052))
                        Text(action.value, style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.SemiBold)
                    }
                }
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
        formOptions = result.bootstrap.formOptions,
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
        formOptions = state.formOptions,
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
        formOptions = state.formOptions,
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
    val asset = selectedWaterAsset(state)
    val waterLevel = if (asset?.assetType in state.formOptions.waterLevelAssetTypes) state.waterLevel else ""
    repo.queueWaterStatus(farm.id, state.selectedWaterAssetId, state.waterStatus, waterLevel, state.waterActive)
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
    val note = state.taskComment.trim()
    if (status == "closed" && note.isBlank()) {
        return state.copy(statusMessage = "Task note is required before closing ${task.displayKey}.")
    }
    repo.queueTaskStatus(farm.id, task.id, status, note.ifBlank { "Mobile shortcut" })
    return state.copy(
        taskComment = if (status == "closed") "" else state.taskComment,
        statusMessage = "Queued ${task.displayKey} status update.",
    )
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

private fun defaultMobileFormOptions(): MobileFormOptions {
    val waterLevels = listOf("empty", "low", "half", "high", "full").map { MobileOption(it, it.replace("_", " ").replaceFirstChar(Char::titlecase)) }
    fun options(vararg values: String): List<MobileOption> =
        values.map { MobileOption(it, it.replace("_", " ").replaceFirstChar(Char::titlecase)) }
    return MobileFormOptions(
        taskStatuses = options("todo", "selected_for_execution", "in_progress", "impeded", "ready_for_verification", "verification_in_progress", "closed"),
        taskPriorities = options("lowest", "low", "high", "highest"),
        waterStatusOptionsByType = mapOf(
            "borehole" to options("operational", "limited", "dry"),
            "pit" to options("operational", "limited", "dry"),
            "windmill" to options("operational", "service_due", "down"),
            "solarpump" to options("operational", "service_due", "down"),
            "cement_dam" to options("operational", "leaking", "damaged"),
            "tank" to options("operational", "leaking", "damaged"),
            "ground_dam" to options("operational", "silted", "damaged"),
            "weir" to options("operational", "silted", "damaged"),
            "trough" to options("operational", "leaking", "damaged"),
        ),
        waterLevelAssetTypes = setOf("pit", "cement_dam", "tank", "ground_dam", "weir", "trough"),
        waterLevelOptions = waterLevels,
    )
}
