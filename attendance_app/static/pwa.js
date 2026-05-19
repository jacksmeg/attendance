(function () {
    const installButton = document.querySelector("[data-pwa-install]");
    const iosSheet = document.querySelector("[data-pwa-ios-sheet]");
    const iosCloseButtons = Array.from(document.querySelectorAll("[data-pwa-ios-close]"));
    const serviceWorkerMeta = document.querySelector('meta[name="pwa-sw-url"]');
    const serviceWorkerUrl = serviceWorkerMeta?.content || "/service-worker.js";
    const inStandalone = window.matchMedia("(display-mode: standalone)").matches || window.navigator.standalone;
    let deferredInstallPrompt = null;
    const isIOS = /iphone|ipad|ipod/i.test(window.navigator.userAgent);
    const isSafari = /^((?!chrome|android).)*safari/i.test(window.navigator.userAgent);

    const closeIosSheet = function () {
        if (iosSheet) {
            iosSheet.hidden = true;
        }
    };

    const openIosSheet = function () {
        if (iosSheet) {
            iosSheet.hidden = false;
        }
    };

    if ("serviceWorker" in navigator) {
        window.addEventListener("load", function () {
            navigator.serviceWorker.register(serviceWorkerUrl).catch(function () {
                /* Keep the app usable even if SW registration fails. */
            });
        });
    }

    if (installButton && inStandalone) {
        installButton.hidden = true;
    }

    if (installButton && isIOS && isSafari && !inStandalone) {
        installButton.hidden = false;
        installButton.textContent = "Add to iPhone";
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
            if (isIOS && isSafari && !inStandalone) {
                openIosSheet();
            }
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

    iosCloseButtons.forEach(function (button) {
        button.addEventListener("click", closeIosSheet);
    });

    window.addEventListener("appinstalled", function () {
        deferredInstallPrompt = null;
        if (installButton) {
            installButton.hidden = true;
        }
        closeIosSheet();
    });
})();
