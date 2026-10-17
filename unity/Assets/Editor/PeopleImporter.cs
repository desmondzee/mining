using UnityEditor;
using UnityEngine;
using UnityEngine.Rendering;

public class PeopleImporter : AssetPostprocessor
{
    const string Root = "Assets/Models/People/";

    bool IsPeople => assetPath.StartsWith(Root);
    bool IsAnimation => assetPath.Contains("/Animations/");

    void OnPreprocessModel()
    {
        if (!IsPeople) return;
        var importer = (ModelImporter)assetImporter;
        importer.animationType = ModelImporterAnimationType.Human;
        importer.avatarSetup = ModelImporterAvatarSetup.CreateFromThisModel;
        importer.importAnimation = IsAnimation;
        importer.importBlendShapes = false;
    }

    void OnPreprocessAnimation()
    {
        if (!IsPeople || !IsAnimation) return;
        var importer = (ModelImporter)assetImporter;
        var clips = importer.defaultClipAnimations;
        foreach (var clip in clips)
        {
            clip.loopTime = true;
            clip.lockRootRotation = true;
            clip.lockRootHeightY = true;
        }
        importer.clipAnimations = clips;
    }

    void OnPreprocessTexture()
    {
        if (!IsPeople || !assetPath.ToLower().Contains("normal")) return;
        var importer = (TextureImporter)assetImporter;
        importer.textureType = TextureImporterType.NormalMap;
        importer.convertToNormalmap = false;
    }

    void OnPostprocessMaterial(Material material)
    {
        if (!IsPeople) return;
        if (material.HasProperty("_BaseColor"))
            material.SetColor("_BaseColor", Color.white);
        if (!material.name.ToLower().Contains("opacity")) return;
        material.SetFloat("_Surface", 0);
        material.SetFloat("_AlphaClip", 1);
        material.SetFloat("_Cutoff", 0.5f);
        material.SetFloat("_Cull", (float)CullMode.Off);
        material.EnableKeyword("_ALPHATEST_ON");
        material.DisableKeyword("_SURFACE_TYPE_TRANSPARENT");
        material.SetOverrideTag("RenderType", "TransparentCutout");
        material.renderQueue = (int)RenderQueue.AlphaTest;
    }
}
