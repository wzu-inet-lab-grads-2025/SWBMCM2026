# B 题第三问题面与答疑口径核查

## 英文题面第 4–5 页

- **第 4 页定义的角度。** 云台根据估计车位计算指令方位角。原文为 “Define Δψ as the deviation between the commanded bearing angle and the true bearing angle, wrapped into the interval [−180°,180°).” 已核对仓库 PDF 与赛题归档内的同名 PDF 逐字节一致。[英文原题第 4 页][p4] · [组委会赛题发布][release]
- **第 4 页给出的三类误差。** 车辆水平定位误差为 $(\delta_x,\delta_y)=\hat P-P$，两分量独立、零均值正态，各自的标准差为 $(0.3+0.02s)$ m，$s$ 为累计行驶米数。云台零位误差和云台控制误差均为零均值正态，标准差分别为 $0.8^\circ$ 和 $0.6^\circ$，两者彼此独立，且均独立于车辆定位误差。目标位置视为完全已知，车到目标的水平距离固定为 40 m。[英文原题第 4 页][p4]
- **第 4–5 页要求的交付。** 建立 $\Delta\psi$ 的误差传播模型，求使 $\Pr(|\Delta\psi|\le 5^\circ)\ge 0.95$ 成立的最大累计里程，并推导在 80 m 时仍满足同一概率要求的非负参数 $a,b$ 约束，定位标准差模型为 $\sigma(s)=a+bs$。第 5 页还要求说明所用近似或数值方法的适用性并验证结果，接着写明 “Compare the relative contributions of the three error sources to the horizontal bearing deviation, and provide practical deployment insights.” [英文原题第 4 页][p4] · [英文原题第 5 页][p5]

按第 4 页的字面，若“指令”指执行前由估计车位算出的角度，零位与控制误差影响的是实际指向，不改变该指令。第 5 页要求比较三类误差对水平方位偏差的贡献，但没有把定义中的 `commanded` 改为 `actual`，也未明示三源比较与 95% 约束是否针对同一种角偏差。[英文原题第 4 页][p4] · [英文原题第 5 页][p5]

## 官方答疑浏览范围

2026-11-20（北京时间）未登录查看了开赛公告指定的答疑论坛入口、带 B 赛题专区参数的频道页首批公开帖子，以及这两页所列六条与 B 题题意相关的帖子正文和附图。三条带截图的提问涉及第二问（2）的站位、约束与计时，以及第四问的断联历时。另读到关于车体与楼体相切、断联期间后续任务和重调度基线的文字提问。[开赛公告第六节][launch] · [官方答疑入口][forum] · [B 赛题专区][board] · [第二问截图帖][q2] · [第二、四问截图帖][q24] · [第四问截图帖][q4] · [相切帖][tangent] · [断联任务帖][offline] · [重调度基线帖][q4text]

**在上述可读内容中，未找到可核实的第三问官方澄清。** 帖页可见提问和评论入口，评论正文未随本次未登录访问返回，结论不覆盖未显示的评论或后续帖子。[B 赛题专区][board] · [帖子页面示例][q4]

[p4]: <../problem/B/2026_“ShuWei Cup”B_Problem.pdf#page=4>
[p5]: <../problem/B/2026_“ShuWei Cup”B_Problem.pdf#page=5>
[release]: https://www.nmmcm.org.cn/Awards/228.html
[launch]: https://www.nmmcm.org.cn/News/225.html
[forum]: https://pd.qq.com/s/f4pnq4egk?b=9
[board]: https://pd.qq.com/g/pd52619943?subc=745397906
[q2]: https://pd.qq.com/g/pd52619943/post/B_6c0c006b70f60c001441152187301728440X60?subc=745397906
[q24]: https://pd.qq.com/g/pd52619943/post/B_bed7ff6aa0800d001441152188103493870X60?subc=745397906
[q4]: https://pd.qq.com/g/pd52619943/post/B_e4b7ff6a0ac20e001441152187121863520X60?subc=745397906
[q4text]: https://pd.qq.com/g/pd52619943/post/B_bc12006bd8b509001441152191012237580X60?subc=745397906
[tangent]: https://pd.qq.com/g/pd52619943/post/B_a416006bae2a00001441152193266564630X60?subc=745397906
[offline]: https://pd.qq.com/g/pd52619943/post/B_6c01006bb84002001441152186789026900X60?subc=745397906
