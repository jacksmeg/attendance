(function () {
    const installButton = document.querySelector("[data-pwa-install]");
    const inStandalone = window.matchMedia("(display-mode: standalone)").matches || window.navigator.standalone;
    let deferredInstallPrompt = null;

    if ("serviceWorker" in navigator) {
        window.addEventListener("load", function () {
            navigator.serviceWorker.register("/service-worker.js").catch(function () {
                /* Keep the app usable even if SW registration fails. */
            });
        });
    }

    if (installButton && inStandalone) {
        installButton.hidden = true;
    }

    window.addEventListener("beforeinstallprompt", function (event) {
        event.preventDefault();
        deferredInstallPrompt = event;
        if (installButton) {
            installButton.hidden = false;
        }
    });

    installButton?.addEventListener("click", async function () {
        if (!deferredInstallPrompt) {
            return;
        }
        deferredInstallPrompt.prompt();
        try {
            await deferredInstallPrompt.userChoice;
        } finally {
            deferredInstallPrompt = null;
            installButton.hidden = true;
        }
    });

    window.addEventListener("appinstalled", function () {
        deferredInstallPrompt = null;
        if (installButton) {
            installButton.hidden = true;
        }
    });
})();
