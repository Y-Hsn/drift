# NewPipeExtractor parses YouTube with reflection-free code but ships Rhino (JavaScript) and jsoup: keep them whole.
-keep class org.schabi.newpipe.extractor.** { *; }
-keep class org.mozilla.javascript.** { *; }
-keep class org.mozilla.classfile.ClassFileWriter
-dontwarn org.mozilla.javascript.**
-dontwarn javax.script.**
-dontwarn java.beans.**
-dontwarn org.jsoup.**
-dontwarn com.google.re2j.**
-dontwarn org.slf4j.**
# The page calls these through window.DriftNative
-keepclassmembers class io.github.yhsn.drift.Bridge { @android.webkit.JavascriptInterface <methods>; }
-keep class * extends com.google.protobuf.GeneratedMessageLite { *; }
-dontwarn com.google.protobuf.**
