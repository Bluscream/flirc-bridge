# Unraid Deployment

1. Copy `template.xml` into your Unraid templates directory:
   - `/boot/config/plugins/dockerMan/templates-user/`
2. In the Unraid web UI, go to the Docker tab and choose **Add Container**.
3. Select the `flirc-bridge` template, review the settings, and adjust environment variables as needed.
4. Map the USB device that exposes your Flirc hardware (typically `/dev/bus/usb`).
5. Click **Apply** to deploy the container.

The template exposes the web UI on port `8000` and stores the SQLite database at `/mnt/user/appdata/flirc-bridge/patterns.db`. Update the paths to fit your environment.
