using System;
using UnityEditor;
using UnityEditor.Build.Reporting;
using UnityEngine;

public static class PlayerBuilder
{
    const string ScenePath = "Assets/Scenes/Quarry.unity";

    [MenuItem("Tools/Mining/Build macOS Player")]
    public static void BuildMac() => Build(BuildTarget.StandaloneOSX, "Builds/mac/Quarry.app");

    static void Build(BuildTarget target, string path)
    {
        PlayerSettings.productName = "Quarry";
        PlayerSettings.runInBackground = true;
        PlayerSettings.fullScreenMode = FullScreenMode.Windowed;
        PlayerSettings.defaultScreenWidth = 640;
        PlayerSettings.defaultScreenHeight = 360;
        PlayerSettings.resizableWindow = true;
        var report = BuildPipeline.BuildPlayer(new BuildPlayerOptions
        {
            scenes = new[] { ScenePath },
            target = target,
            locationPathName = path,
        });
        if (report.summary.result != BuildResult.Succeeded)
            throw new Exception($"player build {report.summary.result}: {report.summary.totalErrors} errors");
        Debug.Log($"player built: {path} ({report.summary.totalSize / 1e6:F0} MB)");
    }
}
