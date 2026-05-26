(function () {
    const runtimeNode = document.getElementById("staff-runtime-data");
    if (!runtimeNode) {
        return;
    }

    let runtime;
    try {
        runtime = JSON.parse(runtimeNode.textContent || "{}");
    } catch (error) {
        runtime = {};
    }

    const workCounter = runtime.work_counter || {};
    const shiftAlarm = runtime.shift_alarm || {};
    const workHourTargets = Array.from(document.querySelectorAll("[data-work-hours-counter]"));
    const alertButtons = Array.from(document.querySelectorAll("[data-staff-alert-toggle]"));
    const alertIconButtons = Array.from(document.querySelectorAll("[data-staff-alert-icon-button]"));
    const alertLabelButtons = alertButtons.filter((button) => !button.hasAttribute("data-staff-alert-icon-button"));
    let shiftReminderTimer = null;
    let audioContext = null;

    const parseIso = function (value) {
        if (!value) {
            return null;
        }
        const parsed = new Date(value);
        return Number.isNaN(parsed.getTime()) ? null : parsed;
    };

    const formatDuration = function (totalSeconds) {
        const safeSeconds = Math.max(0, Math.floor(totalSeconds));
        const hours = Math.floor(safeSeconds / 3600);
        const minutes = Math.floor((safeSeconds % 3600) / 60);
        const seconds = safeSeconds % 60;
        if (safeSeconds > 0 && safeSeconds < 60) {
            return `${seconds}s`;
        }
        return `${hours}h ${String(minutes).padStart(2, "0")}m ${String(seconds).padStart(2, "0")}s`;
    };

    const updateWorkCounter = function () {
        if (!workHourTargets.length) {
            return;
        }
        const checkInAt = parseIso(workCounter.check_in_iso);
        if (!checkInAt) {
            return;
        }

        const checkOutAt = parseIso(workCounter.check_out_iso);
        const activeBreakStartedAt = parseIso(workCounter.active_break_started_iso);
        const totalBreakMinutes = Number(workCounter.total_break_minutes || 0);
        const now = new Date();
        const endAt = checkOutAt || now;

        let workedSeconds = Math.floor((endAt.getTime() - checkInAt.getTime()) / 1000);
        workedSeconds -= totalBreakMinutes * 60;
        if (activeBreakStartedAt && !checkOutAt) {
            workedSeconds -= Math.floor((now.getTime() - activeBreakStartedAt.getTime()) / 1000);
        }
        const label = formatDuration(workedSeconds);
        workHourTargets.forEach((target) => {
            target.textContent = label;
        });
    };

    const unlockAlertAudio = async function () {
        const AudioContextCtor = window.AudioContext || window.webkitAudioContext;
        if (!AudioContextCtor) {
            return;
        }
        if (!audioContext) {
            audioContext = new AudioContextCtor();
        }
        if (audioContext.state === "suspended") {
            try {
                await audioContext.resume();
            } catch (error) {
                /* ignore */
            }
        }
    };

    const playShiftAlarmTone = function () {
        if (!audioContext) {
            return;
        }
        const now = audioContext.currentTime;
        [0, 0.28, 0.56].forEach((offset, index) => {
            const oscillator = audioContext.createOscillator();
            const gainNode = audioContext.createGain();
            oscillator.type = "sine";
            oscillator.frequency.value = index === 2 ? 1046.5 : 880;
            gainNode.gain.setValueAtTime(0.0001, now + offset);
            gainNode.gain.exponentialRampToValueAtTime(0.18, now + offset + 0.02);
            gainNode.gain.exponentialRampToValueAtTime(0.0001, now + offset + 0.18);
            oscillator.connect(gainNode);
            gainNode.connect(audioContext.destination);
            oscillator.start(now + offset);
            oscillator.stop(now + offset + 0.22);
        });
    };

    const setAlertUi = function (stateLabel, summaryLabel, buttonLabel, enabled) {
        alertLabelButtons.forEach((button) => {
            button.textContent = buttonLabel;
        });
        alertIconButtons.forEach((button) => {
            button.dataset.alertState = stateLabel;
            button.dataset.alertSummary = summaryLabel;
            button.classList.toggle("is-enabled", !!enabled);
            button.setAttribute("title", summaryLabel || stateLabel || "Shift alerts");
            button.setAttribute("aria-label", summaryLabel || stateLabel || "Shift alerts");
        });
    };

    const shiftReminderStorageKey = function () {
        return String(shiftAlarm.storage_key || "shift-reminder");
    };

    const markReminderDelivered = function () {
        window.localStorage.setItem(shiftReminderStorageKey(), "1");
    };

    const reminderAlreadyDelivered = function () {
        return window.localStorage.getItem(shiftReminderStorageKey()) === "1";
    };

    const showShiftReminder = async function () {
        if (!("Notification" in window) || Notification.permission !== "granted") {
            return;
        }
        if (reminderAlreadyDelivered()) {
            setAlertUi("Shift alert sent", "Your 10-minute shift alarm has already been delivered for this shift.", "Alerts Active", true);
            return;
        }

        await unlockAlertAudio();
        if ("vibrate" in navigator) {
            navigator.vibrate([250, 150, 250, 150, 500]);
        }
        playShiftAlarmTone();

        try {
            const registration = await navigator.serviceWorker.ready;
            await registration.showNotification(shiftAlarm.notification_title || "Shift reminder", {
                body: shiftAlarm.notification_body || "Your shift starts in 10 minutes.",
                tag: shiftReminderStorageKey(),
                renotify: true,
                requireInteraction: true,
                vibrate: [250, 150, 250, 150, 500],
                badge: shiftAlarm.icon_url || "/pwa/icon-192.png",
                icon: shiftAlarm.icon_url || "/pwa/icon-192.png",
                data: {
                    url: shiftAlarm.home_url || window.location.pathname,
                },
            });
        } catch (error) {
            /* Keep the UI state even if the browser blocks the notification call. */
        }

        markReminderDelivered();
        setAlertUi("Shift alert sent", "Your phone alert has been delivered for the upcoming shift.", "Alerts Active", true);
    };

    const scheduleShiftReminder = function () {
        window.clearTimeout(shiftReminderTimer);
        if (!shiftAlarm.supported) {
            setAlertUi("Shift alerts unavailable", "This staff profile is missing shift timing, so the app cannot arm a reminder yet.", "Unavailable", false);
            return;
        }
        if (!shiftAlarm.enabled_by_policy) {
            setAlertUi("Mobile clocking disabled", "Your organization has disabled mobile clocking for this staff account.", "Unavailable", false);
            return;
        }
        if (!("Notification" in window) || !("serviceWorker" in navigator)) {
            setAlertUi("Phone alerts unsupported", "This browser does not support installed-app alerts on this device.", "Unsupported", false);
            return;
        }

        const reminderAt = parseIso(shiftAlarm.next_reminder_iso);
        const shiftStartAt = parseIso(shiftAlarm.next_shift_start_iso);
        if (!reminderAt || !shiftStartAt) {
            setAlertUi("Shift alerts unavailable", "The app could not calculate your next reminder time.", "Unavailable", false);
            return;
        }

        if (Notification.permission === "denied") {
            setAlertUi(
                "Alerts blocked on this phone",
                "Allow notifications from the installed app in your phone settings to receive the 10-minute shift alarm.",
                "Blocked",
                false,
            );
            return;
        }

        if (reminderAlreadyDelivered()) {
            setAlertUi(
                "Alerts armed",
                `Next shift starts at ${shiftStartAt.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}. This shift alert was already delivered.`,
                "Alerts Active",
                true,
            );
            return;
        }

        const now = new Date();
        if (Notification.permission === "granted") {
            if (now >= reminderAt && now < shiftStartAt) {
                void showShiftReminder();
                return;
            }
            if (now >= shiftStartAt) {
                setAlertUi("Next alert scheduled", "This shift has already started. The next phone alert will arm automatically for your next shift.", "Alerts Active", true);
                return;
            }
            const delay = reminderAt.getTime() - now.getTime();
            shiftReminderTimer = window.setTimeout(() => {
                void showShiftReminder();
            }, Math.max(delay, 0));
            setAlertUi(
                "Alerts armed",
                `Your phone will alert you at ${reminderAt.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })} for the ${shiftAlarm.shift_start_label || ""} shift.`,
                "Alerts Active",
                true,
            );
            return;
        }

        setAlertUi(
            "Enable shift alarms",
            `Turn on notifications and this app will alert you at ${reminderAt.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })} before your shift.`,
            "Enable Alerts",
            false,
        );
    };

    const requestShiftAlerts = async function () {
        if (!("Notification" in window)) {
            scheduleShiftReminder();
            return;
        }

        if (Notification.permission === "default") {
            const permission = await Notification.requestPermission();
            if (permission === "granted") {
                await unlockAlertAudio();
            }
        } else if (Notification.permission === "granted") {
            await unlockAlertAudio();
        }
        scheduleShiftReminder();
    };

    alertButtons.forEach((button) => {
        button.addEventListener("click", () => {
            void requestShiftAlerts();
        });
    });

    document.addEventListener("visibilitychange", () => {
        if (!document.hidden) {
            scheduleShiftReminder();
            updateWorkCounter();
        }
    });
    window.addEventListener("pageshow", () => {
        scheduleShiftReminder();
        updateWorkCounter();
    });
    window.addEventListener("focus", () => {
        scheduleShiftReminder();
        updateWorkCounter();
    });

    updateWorkCounter();
    window.setInterval(updateWorkCounter, 1000);
    scheduleShiftReminder();
})();
