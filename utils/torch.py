import numpy as np
import torch
import torch.nn.functional as F
from GPUtil import getFirstAvailable, getGPUs
from termcolor import colored
import os
from cv2 import dilate
from .generic import nextpow2


def init_weights(net, init_type='normal', init_gain=0.02):
    """Initialize network weights.

    Parameters:
        net (network)   -- network to be initialized
        init_type (str) -- the name of an initialization method: normal | xavier | kaiming | orthogonal | default
        init_gain (float)    -- scaling factor for normal, xavier and orthogonal.

    We use 'xavier' for the network.
    """
    from termcolor import colored
    
    def init_func(m):  # define a initialization function
        classname = m.__class__.__name__
        if hasattr(m, 'weight') and (classname.find('Conv') != -1 or classname.find('Linear') != -1):
            if init_type == 'normal':
                torch.nn.init.normal_(m.weight.data, 0.0, init_gain)
            elif init_type == 'xavier':
                torch.nn.init.xavier_normal_(m.weight.data, gain=init_gain)
            elif init_type == 'kaiming':
                torch.nn.init.kaiming_normal_(m.weight.data, a=0.2, mode='fan_in')
            elif init_type == 'orthogonal':
                torch.nn.init.orthogonal_(m.weight.data, gain=init_gain)
            else:
                raise NotImplementedError(
                    'initialization method [%s] is not implemented' % init_type)
            if hasattr(m, 'bias') and m.bias is not None:
                torch.nn.init.constant_(m.bias.data, 0.0)
        # BatchNorm Layer's weight is not a matrix; only normal distribution applies.
        elif classname.find('BatchNorm') != -1:
            torch.nn.init.normal_(m.weight.data, 10.0, init_gain * 10)
            torch.nn.init.constant_(m.bias.data, 0.0)
    
    if init_type != 'default':
        net.apply(init_func)
        print(colored('parameters initialized with %s' % init_type, 'cyan'))


def get_noise(shape: tuple or list, noise_type: str) -> torch.Tensor:
    """Build a tensor of a given shape with noise of type `noise_type`."""
    x = torch.zeros(shape)
    
    if noise_type == 'u':
        x.uniform_()
    elif noise_type == 'n':
        x.normal_()
    elif noise_type == 'c':
        x.cauchy_()
    else:
        raise ValueError("Noise type has to be one of [u, n, c]")
    return x


def build_noise_tensor(input_depth, spatial_size, method='noise', noise_type='u', var=1. / 10):
    """Returns a pytorch.Tensor of size (1 x `input_depth` x `spatial_size[0]` x `spatial_size[1]`)
    initialized in a specific way.
    Args:
        input_depth: number of channels in the input noise tensor.
        method: `noise` for 2d convolutional network; `noise3d` for 3D convolutional network.
        spatial_size: spatial size of the tensor to initialize
        noise_type: 'u' for uniform; 'n' for normal
        var: a factor, a noise will be multiplicated by. Basically it is standard deviation scaler.
    """
    if isinstance(spatial_size, int):
        spatial_size = (spatial_size, spatial_size)
    if method == 'noise':
        shape = [1, input_depth, spatial_size[0], spatial_size[1]]
    elif method == 'noise3d':
        assert len(spatial_size) == 3
        shape = [1, input_depth, spatial_size[0], spatial_size[1], spatial_size[2]]
    else:
        assert False
    
    net_input = get_noise(shape, noise_type) * var
    
    return net_input


def np_to_torch(in_content: np.ndarray) -> torch.Tensor:
    """
    Converts image in numpy.array to torch.Tensor.
    From C x W x H [0..1] to  C x W x H [0..1]
    """
    return torch.from_numpy(in_content)


def torch_to_np(in_content: torch.Tensor) -> np.ndarray:
    """
    Converts an image in torch.Tensor format to np.array.
    From 1 x C x W x H [0..1] to  C x W x H [0..1]
    """
    return in_content.detach().cpu().numpy()[0]


def get_params(opt_over, net, net_input, downsampler=None):
    """Returns parameters that we want to optimize over.

    Args:
        opt_over: comma separated list, e.g. "net,input" or "net"
        net: network
        net_input: torch.Tensor that stores input `z`
    """
    opt_over_list = opt_over.split(',')
    params = []
    
    for opt in opt_over_list:
        
        if opt == 'net':
            params += [x for x in net.parameters()]
        elif opt == 'down':
            assert downsampler is not None
            params = [x for x in downsampler.parameters()]
        elif opt == 'input':
            net_input.requires_grad = True
            params += [net_input]
        else:
            assert False, 'what is it?'
    
    return params


def set_gpu(id=-1):
    """
    Set GPU device or select the one with the lowest memory usage (None for
    CPU-only)
    """
    if id is None:
        # CPU only
        print(colored('GPU not selected', 'yellow'))
    else:
        # -1 for automatic choice
        device = id if id != -1 else getFirstAvailable(order='memory')[0]
        try:
            name = getGPUs()[device].name
        except IndexError:
            print('The selected GPU does not exist. Switching to the most '
                  'available one.')
            device = getFirstAvailable(order='memory')[0]
            name = getGPUs()[device].name
        
        print(colored('GPU selected: %d - %s' % (device, name), 'yellow'))
        os.environ["CUDA_VISIBLE_DEVICES"] = str(device)


def get_gpu_name(id: int) -> str:
    name = getGPUs()[id].name
    return '%s (%d)' % (name, id)


def set_seed(seed=0):
    """
        Set the seed of random.
    """
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.cuda.manual_seed(seed)
    
    torch.backends.cudnn.enabled = True
    torch.backends.cudnn.benchmark = True
    torch.backends.cudnn.deterministic = True


def add_rand_mask(mask, perc=0.3):
    """
        add the addictive random missing points to the mask.
        parameter:
            mask -- the mask(2D or 3D), which should be (nt, nx), (nt, nx, ny)
            perc -- the percent of addictive deleting samples

        return:
            the processed new makk
    """
    m = mask.copy()
    points = np.argwhere(m[0] == 1)
    rr = np.random.choice(np.arange(points.shape[0]), int(points.shape[0] * perc), replace=False)
    if m.ndim == 2:
        for p in points[rr]:
            m[:, p[0]] = 0
    else:
        for p in points[rr]:
            m[:, p[0], p[1]] = 0
    return m


def dilate_mask(mask, iterations=1):
    kernel = np.ones((2, 2), np.uint8)
    ddtype = mask.dtype
    device = mask.device
    mask_np = mask.clone().detach().cpu().numpy()
    shape = mask_np.shape
    mask_np = mask_np.squeeze()
    mask_res = np.empty_like(mask_np)
    for i in range(mask_np.shape[0]):
        m = mask_np[i].astype(np.uint8)
        mask_res[i] = dilate(m, kernel, iterations=iterations)
    
    mask_res = torch.from_numpy(mask_res.reshape(shape)).type(ddtype).to(device)
    return mask_res


def data_parallel(module, input, device_ids, output_device):
    replicas = torch.nn.parallel.replicate(module, device_ids)
    inputs = torch.nn.parallel.scatter(input, device_ids)
    replicas = replicas[:len(inputs)]
    outputs = torch.nn.parallel.parallel_apply(replicas, inputs)
    return torch.nn.parallel.gather(outputs, output_device)


class MaskUpdate:
    def __init__(self, mask, threshold, step) -> None:
        self.threshold = threshold
        self.step = step
        self.iter = 0
        self.new_mask = mask
        self.old_mask = mask
    
    def update(self, iiter):
        mask_return = self.old_mask
        if iiter > self.threshold:
            iiter_dil = (iiter - self.threshold) // self.step + 1
            if iiter_dil > self.iter:
                self.old_mask = self.new_mask
                self.new_mask = dilate_mask(self.old_mask)
                self.iter = iiter_dil
            iter_drop = (iiter - self.threshold) % self.step
            p = 1. - 1. / self.step * (iter_drop + 1)
            mask_add = F.dropout(self.new_mask - self.old_mask, p)
            mask_add[mask_add != 0] = 1
            
            mask_return = self.old_mask + mask_add
        return mask_return


class EarlyStopping(object):
    """Stop the optimization when the metrics don't improve.
    Use the `step` method for raising a True to stop.
    
    Arguments:
        patience: number of iterations to wait if the stopping condition is met
        max: maximize the metrics instead of minimize
        min_delta: minimum difference between the best and the actual metrics values to trigger the stopping condition
        percentage: min_delta is provided as a percentage of the best metrics value
    """
    
    def __init__(self, patience: int = 10, max: bool = False, min_delta: float = 0, percentage: bool = False):
        self.mode = 'max' if max else 'min'
        self.min_delta = min_delta
        self.patience = patience
        self.percentage = percentage
        self.best = None
        self.num_bad_epochs = 0
        self.is_better = None
        self._init_is_better()
        self.msg = "\nEarly stopping called, terminating..."
        
        if patience == 0:
            self.is_better = lambda a, b: True
            self.step = lambda a: False
    
    def step(self, metrics) -> bool:
        if self.best is None:
            self.best = metrics
            return False
        
        if torch.isnan(metrics):
            print("Metrics is NaN, terminating...")
            return True
        
        if self.is_better(metrics, self.best):
            self.num_bad_epochs = 0
            self.best = metrics
        else:
            self.num_bad_epochs += 1
        
        if self.num_bad_epochs >= self.patience:
            print(self.msg)
            return True
        
        return False
    
    def _init_is_better(self):
        if self.mode not in {'min', 'max'}:
            raise ValueError('mode ' + self.mode + ' is unknown!')
        if not self.percentage:
            if self.mode == 'min':
                self.is_better = lambda a, best: a < best - self.min_delta
            if self.mode == 'max':
                self.is_better = lambda a, best: a > best + self.min_delta
        else:
            if self.mode == 'min':
                self.is_better = lambda a, best: a < best - (best * self.min_delta / 100)
            if self.mode == 'max':
                self.is_better = lambda a, best: a > best + (best * self.min_delta / 100)


__all__ = [
    "init_weights",
    "get_noise",
    "np_to_torch",
    "torch_to_np",
    "get_params",
    "set_gpu",
    "get_gpu_name",
    "set_seed",
    "add_rand_mask",
    "dilate_mask",
    "MaskUpdate",
    "EarlyStopping",
]
