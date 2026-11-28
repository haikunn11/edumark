import fs from 'node:fs';
import path from 'node:path';

const root = process.cwd();
const androidRes = path.join(root, 'android', 'app', 'src', 'main', 'res');
const manifestPath = path.join(root, 'android', 'app', 'src', 'main', 'AndroidManifest.xml');
const mainActivityPath = path.join(root, 'android', 'app', 'src', 'main', 'java', 'vn', 'edumark', 'ai', 'MainActivity.java');

// 1. Patch AndroidManifest.xml with camera, storage & cleartext permissions
if (fs.existsSync(manifestPath)) {
  let xml = fs.readFileSync(manifestPath, 'utf8');

  const permissions = [
    '<uses-permission android:name="android.permission.CAMERA" />',
    '<uses-permission android:name="android.permission.READ_MEDIA_IMAGES" />',
    '<uses-permission android:name="android.permission.READ_EXTERNAL_STORAGE" android:maxSdkVersion="32" />',
    '<uses-feature android:name="android.hardware.camera" android:required="false" />',
  ];

  for (const perm of permissions) {
    const key = perm.match(/android:name="([^"]+)"/)?.[1];
    if (key && !xml.includes(key)) {
      xml = xml.replace('<application', `    ${perm}\n    <application`);
    }
  }

  if (!xml.includes('android:usesCleartextTraffic=')) {
    xml = xml.replace('<application', '<application android:usesCleartextTraffic="true"');
  }

  fs.writeFileSync(manifestPath, xml, 'utf8');
  console.log('Patched AndroidManifest.xml');
} else {
  throw new Error(`Missing AndroidManifest.xml at ${manifestPath}`);
}

// 2. Patch MainActivity.java to support media autoplay, mixed content & Excel/ZIP download links
if (fs.existsSync(mainActivityPath)) {
  const javaSrc = `package vn.edumark.ai;

import android.content.Intent;
import android.net.Uri;
import android.os.Bundle;
import android.webkit.DownloadListener;
import android.webkit.WebSettings;
import android.webkit.WebView;
import com.getcapacitor.BridgeActivity;

public class MainActivity extends BridgeActivity {
    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        if (this.bridge != null && this.bridge.getWebView() != null) {
            WebView webView = this.bridge.getWebView();
            WebSettings ws = webView.getSettings();
            ws.setDomStorageEnabled(true);
            ws.setMediaPlaybackRequiresUserGesture(false);
            ws.setMixedContentMode(WebSettings.MIXED_CONTENT_NEVER_ALLOW);
            webView.setDownloadListener(new DownloadListener() {
                @Override
                public void onDownloadStart(String url, String userAgent, String contentDisposition, String mimetype, long contentLength) {
                    if (url != null && (url.startsWith("https://") || url.startsWith("http://"))) {
                        try {
                            Intent intent = new Intent(Intent.ACTION_VIEW, Uri.parse(url));
                            startActivity(intent);
                        } catch (Exception ignored) {
                        }
                    }
                }
            });
        }
    }
}
`;
  fs.writeFileSync(mainActivityPath, javaSrc, 'utf8');
  console.log('Patched MainActivity.java');
} else {
  throw new Error(`Missing MainActivity.java at ${mainActivityPath}`);
}

// 3. Copy generated EduMark AI mipmap icons and remove anydpi-v26 adaptive XML override
const iconsSrc = path.join(root, 'assets', 'icons');
if (fs.existsSync(iconsSrc)) {
  for (const dir of fs.readdirSync(iconsSrc)) {
    const srcDir = path.join(iconsSrc, dir);
    const dstDir = path.join(androidRes, dir);
    if (fs.statSync(srcDir).isDirectory()) {
      fs.mkdirSync(dstDir, { recursive: true });
      for (const file of fs.readdirSync(srcDir)) {
        fs.copyFileSync(path.join(srcDir, file), path.join(dstDir, file));
      }
    }
  }
  const anydpi = path.join(androidRes, 'mipmap-anydpi-v26');
  if (fs.existsSync(anydpi)) {
    fs.rmSync(anydpi, { recursive: true, force: true });
  }
  console.log('Copied EduMark AI launcher icons');
}
