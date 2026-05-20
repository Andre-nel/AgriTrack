package com.agritrack.mobile;

import android.app.Activity;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.text.InputType;
import android.view.View;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;

import com.agritrack.mobile.data.MobileApiClient;
import com.agritrack.mobile.data.MobileCommand;
import com.agritrack.mobile.data.OfflineCommandQueue;
import com.agritrack.mobile.data.SecureTokenStore;

import org.json.JSONArray;
import org.json.JSONObject;

import java.time.LocalDate;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

public final class MainActivity extends Activity {
    private final ExecutorService background = Executors.newSingleThreadExecutor();
    private final Handler main = new Handler(Looper.getMainLooper());

    private EditText baseUrlInput;
    private EditText emailInput;
    private EditText passwordInput;
    private TextView statusView;
    private MobileApiClient apiClient;
    private SecureTokenStore tokenStore;
    private OfflineCommandQueue queue;
    private String selectedFarmId;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        tokenStore = new SecureTokenStore(this);
        queue = new OfflineCommandQueue(new OfflineCommandQueue.SharedPreferencesCommandStore(this));
        setContentView(buildView());
    }

    private View buildView() {
        ScrollView scroll = new ScrollView(this);
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        int padding = dp(18);
        root.setPadding(padding, padding, padding, padding);
        scroll.addView(root);

        TextView title = new TextView(this);
        title.setText("AgriTrack Field Ops");
        title.setTextSize(26);
        title.setTextColor(0xFF26351F);
        root.addView(title);

        baseUrlInput = input("Base URL", "http://10.0.2.2:5000", false);
        emailInput = input("Email", "", false);
        passwordInput = input("Password", "", true);
        root.addView(baseUrlInput);
        root.addView(emailInput);
        root.addView(passwordInput);

        Button loginButton = button("Login + Load Farm Snapshot");
        loginButton.setOnClickListener(v -> loginAndLoad());
        root.addView(loginButton);

        Button queueRainfallButton = button("Queue Sample Rainfall Offline");
        queueRainfallButton.setOnClickListener(v -> queueSampleRainfall());
        root.addView(queueRainfallButton);

        Button syncButton = button("Sync Offline Queue");
        syncButton.setOnClickListener(v -> syncQueue());
        root.addView(syncButton);

        statusView = new TextView(this);
        statusView.setTextSize(15);
        statusView.setText("Ready. Create a mobile user in Flask, then log in.");
        root.addView(statusView);
        return scroll;
    }

    private EditText input(String hint, String text, boolean password) {
        EditText input = new EditText(this);
        input.setHint(hint);
        input.setText(text);
        input.setSingleLine(true);
        if (password) {
            input.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_PASSWORD);
        }
        return input;
    }

    private Button button(String text) {
        Button button = new Button(this);
        button.setText(text);
        return button;
    }

    private void loginAndLoad() {
        setStatus("Logging in...");
        background.execute(() -> {
            try {
                apiClient = new MobileApiClient(baseUrlInput.getText().toString());
                JSONObject login = apiClient.login(
                        emailInput.getText().toString(),
                        passwordInput.getText().toString(),
                        "Android Field Phone"
                );
                String token = login.getString("token");
                tokenStore.save(token);

                JSONObject bootstrap = apiClient.bootstrap(token);
                JSONArray farms = bootstrap.getJSONArray("farms");
                if (farms.length() == 0) {
                    setStatus("Login worked, but this user has no farm access yet.");
                    return;
                }

                JSONObject farm = farms.getJSONObject(0);
                selectedFarmId = farm.getString("id");
                JSONObject snapshot = apiClient.farmSnapshot(token, selectedFarmId);
                int paddockCount = snapshot.getJSONArray("paddocks").length();
                int mobCount = snapshot.getJSONArray("mobs").length();
                int waterCount = snapshot.getJSONArray("water_assets").length();
                setStatus(
                        "Loaded " + farm.getString("name") + "\n"
                                + paddockCount + " paddock(s), "
                                + mobCount + " mob(s), "
                                + waterCount + " water asset(s).\n"
                                + "Offline queue: " + queue.size()
                );
            } catch (Exception exc) {
                setStatus("Login/load failed: " + exc.getMessage());
            }
        });
    }

    private void queueSampleRainfall() {
        if (selectedFarmId == null) {
            setStatus("Load a farm snapshot before queueing field actions.");
            return;
        }
        try {
            MobileCommand command = MobileCommand.rainfall(
                    selectedFarmId,
                    LocalDate.now().toString(),
                    1.0,
                    "Queued from Android MVP"
            );
            queue.enqueue(command);
            setStatus("Queued rainfall command. Offline queue: " + queue.size());
        } catch (Exception exc) {
            setStatus("Queue failed: " + exc.getMessage());
        }
    }

    private void syncQueue() {
        setStatus("Syncing " + queue.size() + " queued command(s)...");
        background.execute(() -> {
            try {
                if (apiClient == null) {
                    apiClient = new MobileApiClient(baseUrlInput.getText().toString());
                }
                String token = tokenStore.load();
                if (token == null) {
                    setStatus("No stored token. Log in first.");
                    return;
                }
                JSONObject response = apiClient.syncCommands(token, queue.pendingJson());
                JSONArray results = response.getJSONArray("results");
                queue.removeApplied(results);
                setStatus("Sync complete. Remaining queue: " + queue.size() + "\n" + results);
            } catch (Exception exc) {
                setStatus("Sync failed: " + exc.getMessage());
            }
        });
    }

    private void setStatus(String message) {
        main.post(() -> statusView.setText(message));
    }

    private int dp(int value) {
        return Math.round(value * getResources().getDisplayMetrics().density);
    }
}
