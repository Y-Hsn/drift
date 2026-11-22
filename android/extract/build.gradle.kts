// YouTube search / radio / audio stream links through NewPipeExtractor (GPL-3.0). Plain Java so the same code runs
// in the app and in the JVM test (gradlew :extract:test).
plugins { `java-library` }

tasks.withType<JavaCompile>().configureEach { options.release.set(17) }

dependencies {
    api("com.github.TeamNewPipe:NewPipeExtractor:v0.27.1")
    implementation("com.squareup.okhttp3:okhttp:4.12.0")
    testImplementation("junit:junit:4.13.2")
}

tasks.test {
    testLogging { events("passed", "failed"); showStandardStreams = true; exceptionFormat = org.gradle.api.tasks.testing.logging.TestExceptionFormat.FULL }
    outputs.upToDateWhen { false }          // it talks to YouTube: always run it
}
