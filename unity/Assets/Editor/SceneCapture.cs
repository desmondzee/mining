using System;
using System.IO;
using System.Linq;
using MineSim;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;

public static class SceneCapture
{
    const string ScenePath = "Assets/Scenes/Quarry.unity";
    const int Width = 1280;
    const int Height = 720;
    const float WalkSampleTime = 0.4f;
    const float FarClip = 2000f;

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

            var world = UnityEngine.Object.FindFirstObjectByType<MineWorldRoot>();
            if (world == null || world.groundCollider == null) return;
            var b = world.groundCollider.bounds;
            Inspect(b.center, new Vector3(-0.6f, 0, -0.8f), b.extents.x * 1.3f, b.extents.x * 0.9f, Path.Combine(dir, "world_oblique.png"));
            Inspect(truck.position + Vector3.up * 3f, (truck.right * 0.3f - truck.forward).normalized, 45f, 18f, Path.Combine(dir, "trucks_spawn.png"));
            for (int i = 0; GameObject.Find($"Path_{i}") is GameObject path; i++)
            {
                var a = path.transform.GetChild(0).position;
                var c = path.transform.GetChild(path.transform.childCount - 1).position;
                var along = Vector3.Cross(c - a, Vector3.up).normalized;
                Inspect((a + c) / 2 + Vector3.up, along, 30f, 6f, Path.Combine(dir, $"crossing_{i}.png"));
            }
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
            cam.farClipPlane = FarClip;
            cam.cullingMask = ~(1 << LayerMask.NameToLayer("MineDebug"));
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
