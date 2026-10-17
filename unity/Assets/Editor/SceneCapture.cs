using System;
using System.IO;
using System.Linq;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;

public static class SceneCapture
{
    const string ScenePath = "Assets/Scenes/Quarry.unity";
    const int Width = 1280;
    const int Height = 720;
    const float WalkSampleTime = 0.4f;

    [MenuItem("Tools/Mining/Capture Views")]
    public static void Capture()
    {
        var dir = Environment.GetEnvironmentVariable("CAPTURE_DIR") ?? "Captures";
        Directory.CreateDirectory(dir);
        EditorSceneManager.OpenScene(ScenePath);

        AnimationMode.StartAnimationMode();
        try
        {
            foreach (var animator in UnityEngine.Object.FindObjectsByType<Animator>(FindObjectsSortMode.None))
            {
                var clip = animator.runtimeAnimatorController?.animationClips.FirstOrDefault();
                if (clip != null) AnimationMode.SampleAnimationClip(animator.gameObject, clip, WalkSampleTime);
            }

            foreach (var sensor in UnityEngine.Object.FindObjectsByType<SensorCamera>(FindObjectsSortMode.None))
            {
                var cam = sensor.GetComponent<Camera>();
                cam.fieldOfView = Camera.HorizontalToVerticalFieldOfView(sensor.horizontalFov, (float)sensor.width / sensor.height);
                Render(cam, sensor.width, sensor.height, Path.Combine(dir, $"{sensor.cameraName}.png"));
            }

            var person = GameObject.Find("Pedestrian_00").transform;
            Inspect(person.position + Vector3.up * 1.0f, person.forward, 4f, 1.4f, Path.Combine(dir, "pedestrian_front.png"));
            Inspect(person.position + Vector3.up * 1.0f, person.right, 4f, 1.4f, Path.Combine(dir, "pedestrian_side.png"));

            var truck = GameObject.Find("truck-0").transform;
            var diagonal = (truck.forward + truck.right).normalized;
            Inspect(truck.position + Vector3.up * 2.5f, diagonal, 22f, 5f, Path.Combine(dir, "truck_front_right.png"));
            Inspect(truck.position + Vector3.up * 2.5f, truck.right, 22f, 3f, Path.Combine(dir, "truck_side.png"));
        }
        finally
        {
            AnimationMode.StopAnimationMode();
        }
    }

    static void Inspect(Vector3 target, Vector3 direction, float distance, float height, string file)
    {
        var go = new GameObject("InspectCamera");
        try
        {
            var cam = go.AddComponent<Camera>();
            cam.fieldOfView = 40f;
            go.transform.position = target + direction * distance + Vector3.up * (height - 1f);
            go.transform.LookAt(target);
            Render(cam, Width, Height, file);
        }
        finally
        {
            UnityEngine.Object.DestroyImmediate(go);
        }
    }

    static void Render(Camera cam, int width, int height, string file)
    {
        var rt = RenderTexture.GetTemporary(width, height, 24, RenderTextureFormat.ARGB32);
        var previous = cam.targetTexture;
        cam.targetTexture = rt;
        cam.Render();
        RenderTexture.active = rt;
        var tex = new Texture2D(width, height, TextureFormat.RGB24, false);
        tex.ReadPixels(new Rect(0, 0, width, height), 0, 0);
        tex.Apply();
        File.WriteAllBytes(file, tex.EncodeToPNG());
        RenderTexture.active = null;
        cam.targetTexture = previous;
        RenderTexture.ReleaseTemporary(rt);
        UnityEngine.Object.DestroyImmediate(tex);
    }
}
