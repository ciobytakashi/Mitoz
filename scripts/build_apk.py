
#!/usr/bin/env python3

import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

REPO = Path.cwd()
BUILD = Path(os.environ.get("BUILD_ROOT", "/tmp/mitos-build"))
EXTRACT = BUILD / "source"
OUTPUT = BUILD / "output"

APP_NAME = os.environ.get("APP_NAME", "MITOS")
PACKAGE_ID = os.environ.get("PACKAGE_ID", "com.mitos.app")

CFG = (
    json.loads((REPO / "builder.json").read_text())
    if (REPO / "builder.json").exists()
    else {}
)


def run(args, cwd):
    print("+", " ".join(map(str, args)), flush=True)
    subprocess.run(args, cwd=cwd, check=True)


def extract_safe(zpath, dest):
    base = dest.resolve()

    with zipfile.ZipFile(zpath) as z:
        for info in z.infolist():
            target = (dest / info.filename).resolve()

            if target != base and base not in target.parents:
                raise RuntimeError("Unsafe ZIP path: " + info.filename)

        z.extractall(dest)


def find_android(root):
    settings = (
        list(root.rglob("settings.gradle"))
        + list(root.rglob("settings.gradle.kts"))
    )

    for f in settings:
        if ".gradle" not in f.parts:
            d = f.parent

            if (
                (d / "app/build.gradle").exists()
                or (d / "app/build.gradle.kts").exists()
            ):
                return d

    return None


def find_site(root):
    entry = CFG.get("web_entry", "index.html")

    hits = [
        p for p in root.rglob(entry)
        if not any(
            x in p.parts
            for x in (".git", "node_modules", ".gradle")
        )
    ]

    if not hits:
        hits = [
            p for p in root.rglob("index.htm")
            if "node_modules" not in p.parts
        ]

    if not hits:
        raise RuntimeError(
            "No Android Gradle project or website entry file "
            "(index.html/index.htm) found."
        )

    return sorted(
        (p.parent for p in hits),
        key=lambda p: len(p.parts)
    )[0]


def make_webview_shell(site):
    project = BUILD / "webview-shell"

    if project.exists():
        shutil.rmtree(project)

    assets = project / "app/src/main/assets/site"
    assets.mkdir(parents=True)

    shutil.copytree(site, assets, dirs_exist_ok=True)

    package_path = Path(*PACKAGE_ID.split("."))
    java_dir = project / "app/src/main/java" / package_path
    java_dir.mkdir(parents=True)

    res = project / "app/src/main/res/values"
    res.mkdir(parents=True)

    app_label = (
        APP_NAME.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )

    (project / "settings.gradle").write_text(
        "pluginManagement { repositories { google(); mavenCentral(); "
        "gradlePluginPortal() } }\n"
        "dependencyResolutionManagement { "
        "repositoriesMode.set(RepositoriesMode.FAIL_ON_PROJECT_REPOS); "
        "repositories { google(); mavenCentral() } }\n"
        "rootProject.name='MITOSWebShell'\n"
        "include ':app'\n"
    )

    (project / "build.gradle").write_text(
        "plugins { id 'com.android.application' "
        "version '8.7.3' apply false }\n"
    )

    (project / "gradle.properties").write_text(
        "org.gradle.jvmargs=-Xmx2g -Dfile.encoding=UTF-8\n"
        "android.useAndroidX=true\n"
    )

    (project / "app").mkdir(exist_ok=True)

    (project / "app/build.gradle").write_text(
        "plugins { id 'com.android.application' }\n"
        "android {\n"
        f" namespace '{PACKAGE_ID}'\n"
        " compileSdk 35\n"
        " defaultConfig {\n"
        f"  applicationId '{PACKAGE_ID}'\n"
        "  minSdk 23\n"
        "  targetSdk 35\n"
        f"  versionCode {int(CFG.get('version_code', 1))}\n"
        f"  versionName '{CFG.get('version_name', '1.0')}'\n"
        " }\n"
        "}\n"
    )

    (res / "strings.xml").write_text(
        f'<resources><string name="app_name">'
        f'{app_label}</string></resources>\n'
    )

    (project / "app/src/main/AndroidManifest.xml").write_text(
        '<manifest xmlns:android="http://schemas.android.com/apk/res/android">\n'
        ' <uses-permission android:name="android.permission.INTERNET"/>\n'
        ' <application android:theme="@android:style/Theme.Material.Light.NoActionBar" '
        'android:label="@string/app_name" '
        'android:usesCleartextTraffic="true">\n'
        '  <activity android:name=".MainActivity" '
        'android:exported="true">\n'
        '   <intent-filter>'
        '<action android:name="android.intent.action.MAIN"/>'
        '<category android:name="android.intent.category.LAUNCHER"/>'
        '</intent-filter>\n'
        '  </activity>\n'
        ' </application>\n'
        '</manifest>\n'
    )

    java_code = f"""
package {PACKAGE_ID};

import android.app.Activity;
import android.os.Bundle;
import android.content.Intent;
import android.net.Uri;
import android.webkit.*;
import java.util.ArrayList;

public class MainActivity extends Activity {{

    private static final int FILE_CHOOSER_REQUEST = 1001;
    private ValueCallback<Uri[]> filePathCallback;

    @Override
    public void onCreate(Bundle b) {{
        super.onCreate(b);

        WebView w = new WebView(this);
        WebSettings s = w.getSettings();

        s.setJavaScriptEnabled(
            {str(bool(CFG.get('allow_javascript', True))).lower()}
        );
        s.setDomStorageEnabled(true);
        s.setAllowFileAccess(true);
        s.setAllowContentAccess(true);

        w.setWebViewClient(new WebViewClient());

        w.setWebChromeClient(new WebChromeClient() {{
            @Override
            public boolean onShowFileChooser(
                WebView webView,
                ValueCallback<Uri[]> callback,
                FileChooserParams params
            ) {{
                if (filePathCallback != null) {{
                    filePathCallback.onReceiveValue(null);
                }}

                filePathCallback = callback;

                try {{
                    Intent intent = new Intent(Intent.ACTION_OPEN_DOCUMENT);
                    intent.addCategory(Intent.CATEGORY_OPENABLE);
                    intent.setType("*/*");
                    intent.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION);

                    String[] types = params.getAcceptTypes();
                    ArrayList<String> mimeTypes = new ArrayList<>();

                    if (types != null) {{
                        for (String type : types) {{
                            if (type != null && !type.isEmpty()) {{
                                mimeTypes.add(type);
                            }}
                        }}
                    }}

                    if (!mimeTypes.isEmpty()) {{
                        intent.putExtra(
                            Intent.EXTRA_MIME_TYPES,
                            mimeTypes.toArray(new String[0])
                        );
                    }}

                    intent.putExtra(
                        Intent.EXTRA_ALLOW_MULTIPLE,
                        params.getMode() == FileChooserParams.MODE_OPEN_MULTIPLE
                    );

                    startActivityForResult(intent, FILE_CHOOSER_REQUEST);
                    return true;

                }} catch (Exception e) {{
                    filePathCallback = null;
                    callback.onReceiveValue(null);
                    return false;
                }}
            }}
        }});

        setContentView(w);
        w.loadUrl(
            "file:///android_asset/site/{CFG.get('web_entry', 'index.html')}"
        );
    }}

    @Override
    protected void onActivityResult(
        int requestCode,
        int resultCode,
        Intent data
    ) {{
        super.onActivityResult(requestCode, resultCode, data);

        if (requestCode == FILE_CHOOSER_REQUEST) {{
            if (filePathCallback == null) return;

            Uri[] results = null;

            if (resultCode == Activity.RESULT_OK && data != null) {{
                if (data.getClipData() != null) {{
                    int count = data.getClipData().getItemCount();
                    results = new Uri[count];

                    for (int i = 0; i < count; i++) {{
                        results[i] = data.getClipData().getItemAt(i).getUri();
                    }}
                }} else if (data.getData() != null) {{
                    results = new Uri[] {{ data.getData() }};
                }}
            }}

            filePathCallback.onReceiveValue(results);
            filePathCallback = null;
        }}
    }}
}}
"""

    (java_dir / "MainActivity.java").write_text(java_code)
    return project


def main():
    BUILD.mkdir(parents=True, exist_ok=True)
    OUTPUT.mkdir(parents=True, exist_ok=True)

    input_dir = REPO / "input"
    source = input_dir / "app.zip"

    if not source.is_file():
        available = sorted(p.name for p in input_dir.glob("*.zip"))
        raise RuntimeError(
            "Build input not found: input/app.zip. "
            "Rename the ZIP you want to build to app.zip. "
            "Other ZIP files are kept untouched. "
            "Available ZIP files: " + ", ".join(available)
        )

    if EXTRACT.exists():
        shutil.rmtree(EXTRACT)

    EXTRACT.mkdir(parents=True)

    print("Selected build input:", source)
    extract_safe(source, EXTRACT)

    android = find_android(EXTRACT)

    if android:
        print("Detected Android Gradle source project:", android)
        gradlew = android / "gradlew"

        if gradlew.exists():
            gradlew.chmod(0o755)
            run([str(gradlew), "--no-daemon", ":app:assembleDebug"], android)
        else:
            run(["gradle", "--no-daemon", ":app:assembleDebug"], android)

        candidates = list(android.rglob("build/outputs/apk/debug/*.apk"))

        if not candidates:
            raise RuntimeError(
                "Android build ran but no debug APK was produced."
            )

        apk = candidates[0]

    else:
        site = find_site(EXTRACT)
        print("Detected website/HTML project:", site)

        project = make_webview_shell(site)
        run(["gradle", "--no-daemon", "assembleDebug"], project)

        apk = project / "app/build/outputs/apk/debug/app-debug.apk"

        if not apk.exists():
            raise RuntimeError(
                "WebView build ran but app-debug.apk was not produced."
            )

    dest = OUTPUT / (APP_NAME.replace(" ", "-") + "-debug.apk")
    shutil.copy2(apk, dest)

    print("APK_READY:", dest, dest.stat().st_size, "bytes")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("::error::" + str(e), file=sys.stderr)
        raise
        
