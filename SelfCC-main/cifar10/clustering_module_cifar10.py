from torch import nn
from torchvision.models.resnet import Bottleneck, BasicBlock, conv1x1
import torch
import torch.nn.functional as F
from torch.nn.parameter import Parameter


class ResNet(nn.Module):
    def __init__(
        self,
        block,
        layers,
        in_channel=3,
        zero_init_residual=False,
        groups=1,
        width_per_group=64,
        replace_stride_with_dilation=None,
        norm_layer=None,
    ):
        super(ResNet, self).__init__()
        if norm_layer is None:
            norm_layer = nn.BatchNorm2d
        self._norm_layer = norm_layer

        self.inplanes = 64
        self.dilation = 1
        if replace_stride_with_dilation is None:
            # each element in the tuple indicates if we should replace
            # the 2x2 stride with a dilated convolution instead
            replace_stride_with_dilation = [False, False, False]
        if len(replace_stride_with_dilation) != 3:
            raise ValueError(
                "replace_stride_with_dilation should be None "
                "or a 3-element tuple, got {}".format(replace_stride_with_dilation)
            )
        self.groups = groups
        self.base_width = width_per_group
        self.conv1 = nn.Conv2d(
            in_channel, self.inplanes, kernel_size=3, stride=1, padding=1, bias=False
        )
        self.bn1 = norm_layer(self.inplanes)
        self.relu = nn.ReLU(inplace=True)
        self.layer1 = self._make_layer(block, 64, layers[0])
        self.layer2 = self._make_layer(
            block, 128, layers[1], stride=2, dilate=replace_stride_with_dilation[0]
        )
        self.layer3 = self._make_layer(
            block, 256, layers[2], stride=2, dilate=replace_stride_with_dilation[1]
        )
        self.layer4 = self._make_layer(
            block, 512, layers[3], stride=2, dilate=replace_stride_with_dilation[2]
        )
        self.avgpool = nn.AdaptiveAvgPool2d((1, 1))
        self.rep_dim = 512 * block.expansion
        for m in self.modules():
            if isinstance(m, nn.Conv2d):
                nn.init.kaiming_normal_(m.weight, mode="fan_out", nonlinearity="relu")
            elif isinstance(m, (nn.BatchNorm2d, nn.GroupNorm)):
                nn.init.constant_(m.weight, 1)
                nn.init.constant_(m.bias, 0)

        # Zero-initialize the last BN in each residual branch,
        # so that the residual branch starts with zeros, and each residual block behaves like an identity.
        # This improves the model by 0.2~0.3% according to https://arxiv.org/abs/1706.02677
        if zero_init_residual:
            for m in self.modules():
                if isinstance(m, Bottleneck):
                    nn.init.constant_(m.bn3.weight, 0)
                elif isinstance(m, BasicBlock):
                    nn.init.constant_(m.bn2.weight, 0)

    def _make_layer(self, block, planes, blocks, stride=1, dilate=False):
        norm_layer = self._norm_layer
        downsample = None
        previous_dilation = self.dilation
        if dilate:
            self.dilation *= stride
            stride = 1
        if stride != 1 or self.inplanes != planes * block.expansion:
            downsample = nn.Sequential(
                conv1x1(self.inplanes, planes * block.expansion, stride),
                norm_layer(planes * block.expansion),
            )

        layers = []
        layers.append(
            block(
                self.inplanes,
                planes,
                stride,
                downsample,
                self.groups,
                self.base_width,
                previous_dilation,
                norm_layer,
            )
        )
        self.inplanes = planes * block.expansion
        for _ in range(1, blocks):
            layers.append(
                block(
                    self.inplanes,
                    planes,
                    groups=self.groups,
                    base_width=self.base_width,
                    dilation=self.dilation,
                    norm_layer=norm_layer,
                )
            )

        return nn.Sequential(*layers)

    def _forward_impl(self, x):
        # See note [TorchScript super()]
        x = self.conv1(x)
        x = self.bn1(x)
        x = self.relu(x)

        x = self.layer1(x)
        x = self.layer2(x)
        x = self.layer3(x)
        x = self.layer4(x)

        x = self.avgpool(x)
        x = torch.flatten(x, 1)
        return x

    def forward(self, x):
        return self._forward_impl(x)


def get_resnet_cifar(name):
    resnet18 = ResNet(block=BasicBlock, layers=[2, 2, 2, 2])
    resnet34 = ResNet(block=BasicBlock, layers=[3, 4, 6, 3])
    resnet50 = ResNet(block=Bottleneck, layers=[3, 4, 6, 3])

    resnets = {
        "ResNet18": resnet18,
        "ResNet34": resnet34,
        "ResNet50": resnet50,
    }

    if name not in resnets.keys():
        raise KeyError(f"{name} is not a valid ResNet version")
    return resnets[name]


class encoder(nn.Module):
    def __init__(self, resnet):
        super(encoder, self).__init__()
        self.resnet = resnet

    def forward(self, x):
        f = self.resnet(x)
        out = F.normalize(f, dim=1)
        return out


class clustering_model(nn.Module):
    def __init__(self, feat_extractor, fuzzifier, class_number, device):
        super(clustering_model, self).__init__()
        self.feat_extractor = feat_extractor
        self.clustering_layer = Parameter(torch.Tensor(512, class_number), requires_grad=True)
        self.fuzzifier = fuzzifier
        self.device = device
        self.class_number = class_number

    def load_init_weight(self, pretrain_path, gpu):
        state_dict = torch.load(pretrain_path, map_location='cuda:{}'.format(gpu))
        self.feat_extractor.load_state_dict(state_dict)

    def forward(self, x):
        latent = self.feat_extractor(x)
        dis = torch.sqrt(torch.sum(torch.square(torch.unsqueeze(latent, dim=1) - self.clustering_layer), dim=2)).t()
        dis = dis ** (-2. / (self.fuzzifier - 1.))
        c = torch.unsqueeze(torch.sum(dis, dim=0), dim=0)
        u_matrix = (dis / torch.mul(torch.ones(size=(self.class_number, 1)).to(self.device), c)).t()
        return u_matrix
