#!/bin/bash
set -e

PROJECT_DIR="/home/user/apks"
SRC_DIR="$PROJECT_DIR/app/src/main"
BUILD_DIR="$PROJECT_DIR/build"
ANDROID_JAR="/usr/lib/android-sdk/platforms/android-23/android.jar"

echo "=== GLOW POP APK BUILD ==="

# Clean
rm -rf "$BUILD_DIR"
mkdir -p "$BUILD_DIR/gen" "$BUILD_DIR/obj" "$BUILD_DIR/apk"

# Step 1: Generate R.java from resources
echo "[1/6] Generating R.java..."
aapt package -f -m \
  -S "$SRC_DIR/res" \
  -M "$SRC_DIR/AndroidManifest.xml" \
  -I "$ANDROID_JAR" \
  -J "$BUILD_DIR/gen" \
  --generate-dependencies

# Step 2: Compile Java sources
echo "[2/6] Compiling Java..."
javac -source 8 -target 8 \
  -bootclasspath "$ANDROID_JAR" \
  -classpath "$ANDROID_JAR" \
  -d "$BUILD_DIR/obj" \
  "$BUILD_DIR/gen/com/glowpop/game/R.java" \
  "$SRC_DIR/java/com/glowpop/game/MainActivity.java" \
  2>&1

# Step 3: Convert to DEX
echo "[3/6] Creating DEX..."
dalvik-exchange --dex \
  --output="$BUILD_DIR/classes.dex" \
  "$BUILD_DIR/obj"

# Step 4: Package resources into APK
echo "[4/6] Packaging resources..."
aapt package -f \
  -S "$SRC_DIR/res" \
  -M "$SRC_DIR/AndroidManifest.xml" \
  -I "$ANDROID_JAR" \
  -F "$BUILD_DIR/glowpop-unsigned.apk" \
  -A "$SRC_DIR/assets"

# Step 5: Add DEX to APK
echo "[5/6] Adding DEX to APK..."
cd "$BUILD_DIR"
aapt add -f "$BUILD_DIR/glowpop-unsigned.apk" classes.dex

# Step 6: Sign APK
echo "[6/6] Signing APK..."
# Generate keystore if needed
if [ ! -f "$PROJECT_DIR/debug.keystore" ]; then
  keytool -genkey -v \
    -keystore "$PROJECT_DIR/debug.keystore" \
    -storepass android \
    -alias androiddebugkey \
    -keypass android \
    -keyalg RSA \
    -keysize 2048 \
    -validity 10000 \
    -dname "CN=Debug, OU=Debug, O=Debug, L=Debug, ST=Debug, C=US"
fi

jarsigner -verbose \
  -sigalg SHA256withRSA \
  -digestalg SHA-256 \
  -keystore "$PROJECT_DIR/debug.keystore" \
  -storepass android \
  -keypass android \
  "$BUILD_DIR/glowpop-unsigned.apk" \
  androiddebugkey

# Copy final APK
cp "$BUILD_DIR/glowpop-unsigned.apk" "$PROJECT_DIR/glowpop.apk"

echo ""
echo "=== BUILD COMPLETE ==="
echo "APK: $PROJECT_DIR/glowpop.apk"
echo "Size: $(du -h "$PROJECT_DIR/glowpop.apk" | cut -f1)"
