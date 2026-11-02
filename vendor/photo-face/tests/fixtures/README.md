`det_10g_synthetic.npz` records all nine float32 ONNX Runtime CPU outputs of
pinned det_10g.onnx (SHA256
5838f7fe053675b1c7a08b633df49e7af5495cee0493c7dcf6697200b85b5b91).
No photographs or personal data are included. ONNX Runtime: 1.23.2, eight intra-op
threads, CPUExecutionProvider. Fixture generation:

```python
y, x = np.mgrid[:640, :640]
image = np.stack((x % 256, y % 256, (x + y) % 256), axis=-1).astype(np.uint8)
blob = cv2.dnn.blobFromImage(image, 1 / 128., (640, 640), (127.5,) * 3, swapRB=True)
outputs = session.run(None, {session.get_inputs()[0].name: blob})
np.savez_compressed(path, **{str(i): value for i, value in enumerate(outputs)})
```

The parity test deliberately uses 0.001 only as a synthetic fixture threshold
so this non-face pattern exercises nonempty decoding/NMS; production defaults
and all face matching thresholds are unchanged. A second handcrafted fixture in
the test exercises overlapping anchors at the normal detector threshold.
