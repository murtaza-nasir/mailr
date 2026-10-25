# The serialization, OkHttp and Tink rules come with android/session-core (consumer-rules.pro).
# Release builds drop debug and verbose logging (and the strings built for it).
-assumenosideeffects class android.util.Log {
    public static int d(...);
    public static int v(...);
}

# Glance matches each widget receiver to its GlanceAppWidget by class name. R8 merged the widget
# classes into one (7 Oct 2026: the Agenda widget turned into the Today widget after a calendar
# change, because TodayWidget().updateAll() then found the Agenda widget's id too). Keep every widget
# class whole and named. tools/android-release checks the mapping for this.
-keep class * extends androidx.glance.appwidget.GlanceAppWidget
