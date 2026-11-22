import org.gradle.api.file.DirectoryProperty
import java.util.Properties

plugins {
    id("com.android.application")
}

// The release key lives outside the repo (Drift-release/android/keys, or DRIFT_ANDROID_KEYS): Android only takes an
// update signed with the same key. Without it, release builds use this PC's debug key.
val keys: File = file(System.getenv("DRIFT_ANDROID_KEYS") ?: "../../../Drift-release/android/keys")
val keyProps = Properties()
keys.resolve("release.properties").takeIf { it.isFile }?.inputStream()?.use { keyProps.load(it) }

android {
    namespace = "io.github.yhsn.drift"
    compileSdk = 36

    defaultConfig {
        applicationId = "io.github.yhsn.drift"
        minSdk = 26
        targetSdk = 36
        versionCode = 1
        versionName = "0.1.0"
    }

    signingConfigs {
        if (keyProps.isNotEmpty()) create("release") {
            storeFile = keys.resolve(keyProps.getProperty("storeFile"))
            storePassword = keyProps.getProperty("storePassword")
            keyAlias = keyProps.getProperty("keyAlias")
            keyPassword = keyProps.getProperty("keyPassword")
        }
    }

    buildTypes {
        release {
            // not minified: the same code as the tested debug build (NewPipeExtractor leans on reflection)
            isMinifyEnabled = false
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
            signingConfig = signingConfigs.findByName("release") ?: signingConfigs.getByName("debug")
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
        isCoreLibraryDesugaringEnabled = true      // NewPipeExtractor uses java.time / java.nio bits older phones lack
    }

    packaging {
        resources.excludes += setOf("META-INF/*.kotlin_module", "META-INF/DEPENDENCIES", "META-INF/LICENSE*",
            "META-INF/NOTICE*", "**/*.proto")
    }
}

// The phone app (../../phone) goes into the APK as it is: the same screens as on an iPhone.
abstract class PhoneAssets : DefaultTask() {
    @get:InputDirectory abstract val source: DirectoryProperty
    @get:OutputDirectory abstract val out: DirectoryProperty

    @TaskAction
    fun copy() {
        val dest = out.get().asFile.resolve("phone")
        dest.deleteRecursively()
        source.get().asFile.copyRecursively(dest)
    }
}

val phoneAssets = tasks.register<PhoneAssets>("phoneAssets") {
    source.set(rootProject.layout.projectDirectory.dir("../phone"))
}

androidComponents {
    onVariants { v -> v.sources.assets?.addGeneratedSourceDirectory(phoneAssets, PhoneAssets::out) }
}

dependencies {
    implementation(project(":extract"))
    implementation("androidx.media3:media3-exoplayer:1.11.1")
    implementation("androidx.media3:media3-session:1.11.1")
    implementation("androidx.media3:media3-datasource-okhttp:1.11.1")
    implementation("androidx.webkit:webkit:1.18.0")
    coreLibraryDesugaring("com.android.tools:desugar_jdk_libs_nio:2.1.5")
}
