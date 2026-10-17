const KEYFRAME = 1;
const MAX_DECODE_QUEUE = 30;
const names = new TextDecoder();

function spsCodec(au) {
  for (let i = 0; i + 4 < au.length; i++) {
    if (au[i] === 0 && au[i + 1] === 0 && au[i + 2] === 1 && (au[i + 3] & 0x1f) === 7) {
      const hex = (b) => b.toString(16).padStart(2, "0");
      return `avc1.${hex(au[i + 4])}${hex(au[i + 5])}${hex(au[i + 6])}`;
    }
  }
  return null;
}

function draw(tile, frame) {
  const c = tile.canvas;
  if (c.width !== frame.displayWidth) c.width = frame.displayWidth;
  if (c.height !== frame.displayHeight) c.height = frame.displayHeight;
  c.getContext("2d").drawImage(frame, 0, 0);
  frame.close();
  tile.frames++;
}

function decoderFor(tile, codec) {
  const state = { codec, decoder: null };
  state.decoder = new VideoDecoder({
    output: (frame) => draw(tile, frame),
    error: (e) => {
      console.warn("video decoder reset", e);
      if (tile.h264 === state) tile.h264 = null;
    },
  });
  state.decoder.configure({ codec, optimizeForLatency: true });
  return state;
}

function onH264(tileFor, ev) {
  const buf = new Uint8Array(ev.data);
  const n = buf[0];
  const tile = tileFor(names.decode(buf.subarray(1, 1 + n)));
  const view = new DataView(ev.data, 1 + n, 5);
  const key = (view.getUint8(0) & KEYFRAME) !== 0;
  const timestamp = view.getUint32(1, true) * 1000;
  const au = buf.subarray(6 + n);

  let s = tile.h264;
  if (s && s.decoder.decodeQueueSize > MAX_DECODE_QUEUE) {
    s.decoder.close();
    s = tile.h264 = null;
  }
  if (key) {
    const codec = spsCodec(au) || "avc1.42e01e";
    if (!s || s.codec !== codec || s.decoder.state === "closed") {
      if (s && s.decoder.state !== "closed") s.decoder.close();
      s = tile.h264 = decoderFor(tile, codec);
    }
  }
  if (!s || s.decoder.state !== "configured") return;
  try {
    s.decoder.decode(new EncodedVideoChunk({ type: key ? "key" : "delta", timestamp, data: au }));
  } catch (e) {
    console.warn("video decode failed", e);
    s.decoder.close();
    tile.h264 = null;
  }
}

async function h264Supported() {
  if (typeof VideoDecoder === "undefined") return false;
  try {
    return (await VideoDecoder.isConfigSupported({ codec: "avc1.42e01e" })).supported;
  } catch {
    return false;
  }
}

export async function videoFeed(tileFor, onJpeg) {
  if (!(await h264Supported())) return { codec: "jpeg", path: "/ws/video?codec=jpeg", onMessage: onJpeg };
  return { codec: "h264", path: "/ws/video", onMessage: (ev) => onH264(tileFor, ev) };
}
