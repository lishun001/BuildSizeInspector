using System;
using UnityEditor;
using UnityEditor.PackageManager;
using UnityEditor.PackageManager.Requests;

public static class BuildSizePackageInstaller
{
    static AddAndRemoveRequest request;
    static double deadline;
    public static void Install()
    {
        string path = Environment.GetEnvironmentVariable("BUILDSIZE_PACKAGE_PATH");
        if (string.IsNullOrEmpty(path)) { EditorApplication.Exit(2); return; }
        request = Client.AddAndRemove(new[] { "file:" + path }, new string[0]);
        deadline = EditorApplication.timeSinceStartup + 180;
        EditorApplication.update += Poll;
    }
    static void Poll()
    {
        if (!request.IsCompleted)
        {
            if (EditorApplication.timeSinceStartup > deadline) EditorApplication.Exit(3);
            return;
        }
        EditorApplication.update -= Poll;
        UnityEngine.Debug.Log("BuildSize package install: " + request.Status);
        EditorApplication.Exit(request.Status == StatusCode.Success ? 0 : 1);
    }
}
