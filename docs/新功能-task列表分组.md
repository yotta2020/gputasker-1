我想要一个把task分组查看的功能
分为两层
project
    group
        task list

现阶段是task list直接平铺显示的，能看到历史所有的task。

我是以论文项目为单位来管理任务的
比如投稿ISSTA2026会议，并且论文项目是 “基于聚类的后门防御方法”
所以project名称就是 “ISSTA2026-基于聚类的后门防御方法”
在这个project下，我可能会有多个group。
探索方法阶段，测试一个方法设想为一个group。
比如，我想测试不同数据集组成对方法效果的影响
我会创建一个group，名称为 “数据集组成影响测试”
在这个group下，我会创建多个task
task1：使用数据集A训练模型
task2：使用数据集B训练模型
task3：使用数据集C训练模型
等等


这样我就可以清晰地看到在“ISSTA2026-基于聚类的后门防御方法”这个project下，不同“数据集组成影响测试”group下的各个task的状态和结果。
另外，我还可以创建其他group，比如“模型类型影响测试”，在这个group下测试不同模型架构对方法效果的影响。

过往的任务都放入project archive , group archive 中，不参与当前项目的管理。