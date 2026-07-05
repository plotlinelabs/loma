// Loma service worker — web push for tasks-board notifications.

self.addEventListener("install", () => {
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(self.clients.claim());
});

self.addEventListener("push", (event) => {
  if (!event.data) return;
  let payload;
  try {
    payload = event.data.json();
  } catch {
    payload = { title: "Loma", body: event.data.text() };
  }
  event.waitUntil(
    self.registration.showNotification(payload.title || "Loma", {
      body: payload.body || "",
      tag: payload.tag,
      data: { url: payload.url },
      icon: "/icons/icon-192.png",
    }),
  );
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const url = event.notification.data && event.notification.data.url;
  if (!url) return;
  event.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((clients) => {
      for (const client of clients) {
        // Reuse an open dashboard tab when we can.
        if (new URL(client.url).origin === self.location.origin && "navigate" in client) {
          client.focus();
          return client.navigate(url);
        }
      }
      return self.clients.openWindow(url);
    }),
  );
});
