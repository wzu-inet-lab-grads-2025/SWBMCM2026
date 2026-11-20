# 2026 年“数维杯”D 题：恒星系统的单一中心性与行星轨道闭合模型

> 译注：下文按原题结构翻译。第一节为命题方陈述的背景和论点；第二节列出建模任务及题设希望展示的结论；第三节为命题方提供的建模提示。译文不对题设论断作独立验证。

## 一、科学问题

1609 年，开普勒提出行星运动三定律。随后，艾萨克·牛顿建立了万有引力定律，为所有行星的运动提供了统一的理论框架，从而为现代天体物理学的发展奠定了坚实基础。然而，随着观测技术日益精确，再考虑多体系统中的牛顿引力动力学与广义相对论所预言的时空弯曲的共同作用，题目指出，开普勒定律在准确性与严谨性方面均有明显不足。

![坐标系：图中标示原点 O、坐标轴 x、y、z、径向距离 r、角度 θ 和 φ，以及轨道曲线。](<原题图/D-坐标系.jpg>)

**图：坐标系**（原图注：Coordinate system）

第一，开普勒使用的轨道观测数据在空间维度上不完备。任何行星天体都在三维空间中运动；题设认为，要确定真实轨道，需要至少从三个相互正交的方向进行观测，这相当于工程中的正投影三视图。然而，在伽利略、第谷和开普勒所处的时代，只能从地球上的单一视点获取观测数据，视线相对固定，位于黄道面内。没有其他视角的观测，尤其缺少垂直于黄道面方向的俯视观测。

因此，开普勒所依据的数据不足以使行星系统状态得到充分观测，从根本上限制了对天体系统状态的完整重建。

第二，观测数据在时间维度上也不完备。第谷积累的观测时间跨度不足，主要观测集中在 1580 年至 1600 年之间。因此，没有观测到、也没有获得能确认一个轨道周期已经完成的完整数据，同时也没有记录到径向周期扰动的一个完整周期。实际的径向共振周期可能非常长，甚至长达几个世纪。从控制理论中的系统辨识角度看，有限时长的时间序列样本不足以充分辨识长周期模态。

第三，二体问题本身包含理想化假设。从平方反比引力定律推导出的严格椭圆轨道（或圆轨道），是数学上可积的二体保守系统的精确解。然而，题目将真实行星系统描述为不可积的多体耗散系统，即使耗散极小，该系统也表现出混沌与共振。因此，椭圆轨道（或圆轨道）本质上是一个降阶的理想化参考模型，并不能完整描述真实多体非线性系统的动力学。

第四，充分条件不能构成充要结论。从圆周运动和椭圆轨道出发推导并证明万有引力定律，不能排除其他非圆锥曲线轨道或复合曲线轨道；其他轨道模型也可能同样适用于描述实际轨迹。因此，该论证是基于某一特定充分条件的推导，而不是基于充要条件对行星动力系统结构参数的辨识 [1–7]。

## 二、研究任务

### 任务 1：进行数学推导，并编程绘图

从地球视角对太阳系中的某颗行星（如金星或火星）的轨道曲线进行遥测和绘制时，观测具有一定角度，可能是俯视，也可能是仰视。因此，观测到的轨道形状，与沿垂直于该行星轨道所在平面的方向（原文为 planet’s orbital ecliptic plane）观察时所描绘的真实轨道之间，存在几何差异。请结合几何原理与光学视差，对这些几何差异进行数学推导，并编程绘制这些轨道曲线。

### 任务 2：建立并发展新的“恒星系统的单一中心性与行星轨道闭合模型”

运用数学和物理方法，建立并发展该模型。题目要求采用双重共振模型，即包含径向和方位角方向的振荡运动。题设希望得到的核心结论是：长期极限下，累计轨迹将形成一个各向同性、均匀且对称的轨道环，并具有单一的动力学中心。

### 任务 3：基于上述方程，用计算机程序展示行星运动的几何轨迹

题设希望通过展示计算得到的行星运动几何轨迹，说明开普勒定律在准确性与严谨性方面存在明显不足。

## 三、解题提示

为求解这一多体问题，可参考牛顿关于阻力介质中螺旋运动的研究 [8]，以及 James Deng 的 SDP 原子模型中的 DTQ 定律 [9–12]，后者展示了径向共振。命题方提出，可将轨道进动视为彼此独立的径向振荡与方位角振荡之和，建立一个参数化的双重共振模型，并保持角动量守恒。题目称，由此可表明，长期累计轨迹收敛到一个各向同性、均匀的环，从而支持单一动力学中心的观点。

## 参考文献

以下保留原题书目信息，括号内为题名中译。

[1] J. L. Russell. “Kepler’s Laws of Planetary Motion: 1609–1666”（开普勒行星运动定律：1609–1666）. *The British Journal for the History of Science*, 2(1), 1–24 (1964).

[2] V. E. Thoren & J. R. Christianson. *The Lord of Uraniborg: A Biography of Tycho Brahe*（乌拉尼堡之主：第谷·布拉赫传）. Cambridge University Press, 1990.

[3] M. Plakhotnyk. “Kepler’s Laws with Introduction to Differential Calculus”（开普勒定律与微分学导论）. *arXiv preprint*, arXiv:1702.06537 (2017).

[4] P. S. marquis de Laplace. *Traité de mécanique céleste*（天体力学论）, Vol. 5. Chez JBM Duprat, libraire pour les mathématiques, quai des Augustins, 1825.

[5] J. L. Lagrange. *Mécanique analytique*（分析力学）, Vol. 1. Mallet-Bachelier, 1853.

[6] H. Poincaré. “Sur le problème des trois corps et les équations de la dynamique”（论三体问题与动力学方程）. *Acta Mathematica*, 13(1), A3–A270 (1890).

[7] A. Einstein. “Die Grundlagen der allgemeinen. Relativitats− teorie”（广义相对论的基础）. *Annale der Physic*, 49, 769 (1916).［原题书目拼写照录。］

[8] I. Newton. *Philosophiae naturalis principia mathematica*（自然哲学的数学原理）, Vol. 1. G. Brookman, 1833.

[9] James T. Q. Deng. “A New Terahertz Source from the Wave Particle Duality Mechanism”（一种基于波粒二象性机制的新型太赫兹源）. *The Journal of Engineering*, 2023(1), e12217 (2023).

[10] James. Deng. “Theoretically Proving the Quantization Hypothesis”（量子化假设的理论证明）. *Authorea Preprints*, 05373575/v1 (2022).

[11] James. Deng. “Theoretically Proving the Quantized Wave-Particle Duality”（量子化波粒二象性的理论证明）. *Authorea Preprints*, 68586916/v1 (2022).

[12] James T. Q. Deng. *Decisive Theory for Quantum*（量子的决定性理论）. ISBN: 979-8-90049-112-7. United Science Press, NY 12204, USA, 2025.
