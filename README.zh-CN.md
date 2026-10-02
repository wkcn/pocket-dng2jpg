# pocket-dng2jpg

[English](README.md)

把 **DJI Osmo Pocket 4P** 的 RAW 照片（`.DNG`）转成和机内 JPG 观感一致的 JPG：颜色、饱和度、亮度和局部影调都与相机直出一致。

## 解决什么问题

如果在 Pocket 4P 上只存了 DNG、没存 JPG，事后自己转换，结果和机内 JPG 差别很明显：要么偏暗要么偏亮，颜色和饱和度不对，红色还会偏洋红。下表是和真实机内 JPG 对比的平均 CIE76 色差 ΔE（小于 1 基本看不出，大于 5 就很明显）：

| 转换方式 | 与机内 JPG 的 ΔE |
|---|---|
| `rawpy` / LibRaw 默认参数 | 3.5 – 37 |
| macOS `sips`（苹果 RAW 引擎） | 3.3 – 23 |
| 按 DNG 规范完整实现（ColorMatrix、HueSatMap、ProfileToneCurve、BaselineExposure） | 4.7 – 18 |
| **pocket-dng2jpg** | **0.47 – 2.2（平均 0.82）** |

通用转换工具对不上的原因：

1. **BaselineExposure 每张都不同**（0 – 4 EV），很多工具直接忽略了它。
2. **相机做了局部影调映射**：根据画面内容提亮暗部、压住高光。任何固定的色彩矩阵或色调曲线都复现不了，所以即使严格按 DNG 规范实现，差距依然很大。
3. **DNG 里没有记录数码变焦**（`DigitalZoomRatio` 永远是 1），但 JPG 是按变焦裁切过的。≥2× 时 DNG 本身已经是传感器裁切，所以 3× 的 JPG 对应 DNG 里再放大 1.5×。

## 怎么解决的

关键发现：**Pocket 4P 的每个 DNG 里都嵌了一张 1280×720 的 `PreviewImage`，它就是机内 JPG 渲染结果的缩小版**（和真实 JPG 的 ΔE 只有 0.55 – 1.3）。它分辨率低，但颜色和影调正是我们想要的；DNG 则提供细节。`convert.py` 把两者结合起来：

1. **线性渲染。** 用 LibRaw 解马赛克，再应用 DNG 的 `ColorMatrix1/2`（按白点插值）、拍摄时的白平衡和 `BaselineExposure`。得到线性 ProPhoto RGB，并做对数编码，保留超过 1.0 的高光。
2. **估计数码变焦。** 在渲染图上搜索与预览图边缘最吻合的居中裁切（拉普拉斯图的归一化互相关，先粗后细）。
3. **在预览图分辨率上学习相机的风格：**
   - **全局三次多项式**色彩映射（渲染图 → 预览图），对应相机的色彩科学和色调曲线；
   - 对残差做**局部仿射修正**，用彩色引导滤波（He et al.）拟合，对应局部影调映射和暗角。
4. **在全分辨率上应用。** 把映射上采样后作用到全分辨率渲染图上，细节来自 RAW。变焦的裁切按相机的方式放大回原尺寸，最后写出 JPG 并从 DNG 拷贝 EXIF。

### 效果

在 30 组 DNG+JPG 上测试（横拍/竖拍、1×–3× 变焦、ISO 50–400、BaselineExposure 0–4 EV）：

- 平均 ΔE **0.82**，和嵌入的预览图本身的误差（0.6–1.3）相当；
- 全分辨率平均 PSNR **37.6 dB**；
- 数码变焦估计与 JPG 的 `DigitalZoomRatio` 吻合（例如 1.242 对 1.24；3× 的照片估计为 1.5，因为传感器已裁切一半）。

在一批 467 张只有 RAW 的真实照片上，输出与各自嵌入预览图的平均 ΔE 为 1.38（95% 分位 2.25）。

## 安装

需要 Python 3.9+，以及 `PATH` 中可用的 [ExifTool](https://exiftool.org/)。

```bash
brew install exiftool          # macOS；Linux: apt install libimage-exiftool-perl
pip install -r requirements.txt
```

## 用法

转换整个文件夹或单个文件：

```bash
python3 convert.py /path/to/DCIM/DJI_001 -o /path/to/output
python3 convert.py a.DNG b.DNG -o out/
```

参数：

| 参数 | 说明 |
|---|---|
| `-o, --outdir DIR` | 输出目录（必填），输出文件名为 `<原名>.JPG`。 |
| `-q, --quality N` | JPEG 质量，默认 `95`。机内用的是 100；95 看不出区别，体积约为一半。 |
| `--keep-fov` | 对数码变焦的照片保留完整传感器画面，而不是像相机那样裁切。裁切区以外只使用全局映射。 |
| `--overwrite` | 重新转换已存在的文件。默认会跳过，所以中断后可以接着跑。 |

在 Apple Silicon 上每张约 5 秒。想用满所有核心，可以并行跑多个进程：

```bash
ls /path/to/DCIM/DJI_001/*.DNG | xargs -n 20 -P 6 python3 convert.py -o /path/to/output
```

### 在自己的相机上评估

用 **DNG+JPG** 模式拍几张照片，把成对的文件放在同一个文件夹里，然后运行：

```bash
python3 evaluate.py /path/to/pairs [--save-dir out/]
```

它会输出每组的 ΔE 和 PSNR，以及嵌入预览图与 JPG 的接近程度。

## 文件说明

| 文件 | 用途 |
|---|---|
| `convert.py` | 转换器（命令行）。 |
| `evaluate.py` | 将转换结果与机内 JPG 对比。 |
| `dngmeta.py` | 用 ExifTool 读取 DNG 的色彩相关标签。 |
| `render.py` | 线性 相机 → XYZ → ProPhoto 渲染，以及一个按 DNG 规范实现的参考渲染器（HueSatMap、ProfileToneCurve）。 |
| `compare.py` | 图像读写和 ΔE 工具函数。 |

## 局限

- **依赖嵌入的预览图。** 测试过的所有 Pocket 4P DNG（固件 10.02.01.08）都有。相机 `MISC/THM/*/*.SCR` 文件里也是同一张图。
- **没有复刻锐化。** 树叶等很细的纹理会比机内 JPG 略软，饱和度也略低一点。
- **只在 Osmo Pocket 4P 上测试过。** 其他在 DNG 里嵌入全彩预览图的相机可能也适用，但未经测试。

## 声明

本项目为独立项目，与 DJI（大疆）无关，也未获其认可。

## 许可证

[MIT](LICENSE)
