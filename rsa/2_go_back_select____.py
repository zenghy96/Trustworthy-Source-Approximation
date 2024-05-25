import json
import profile
import time
import matplotlib.pyplot as plt
import scipy.io as sio
import os
import numpy as np
import torch
from tqdm import tqdm
from torchvision import transforms as T
import sys
import argparse

sys.path.append('.')
# ## unet
from segmentation.models.unet import EvidentialUNet
from segmentation.models.model_tools import load_model
from rsa.dataset.sample_dataset import SampleDataset
from rsa.utils import *


import argparse


def get_args():
    # Create the parser
    parser = argparse.ArgumentParser(description='Your script description here.')
    # Add arguments
    parser.add_argument('--sample_dir', type=str, default='outputs/final_0/samples', help='Directory for samples')
    parser.add_argument('--data_dir', type=str, default='data/VS/T2', help='Data directory')
    parser.add_argument('--split', type=str, default='training', help='Data split to use')

    parser.add_argument('--fig_dir', type=str, default='outputs/final_z/seed125', help='Directory for figures')
    parser.add_argument('--save_dir', type=str, default='outputs/final_z/seed125', help='Directory to save outputs')
    parser.add_argument('--record_save_name', type=str, default='outputs/final_z/seed125.json', help='Directory to save outputs')

    # parser.add_argument('--save_dir', type=str, default='outputs/ablation_uncertainty/norefine', help='Directory to save outputs')
    # parser.add_argument('--fig_dir', type=str, default='outputs/ablation_uncertainty/norefine_show', help='Directory for figures')
    # parser.add_argument('--record_save_name', type=str, default='outputs/ablation_uncertainty/norefine.json', help='Directory to save outputs')

    parser.add_argument('--seg_ckpt_dir', type=str, default='checkpoints/source_seg/seed_125/ckpt/best_val.pth.tar', help='Segmentation checkpoint directory')
    parser.add_argument('--un_thresh', type=float, default=0.2, help='Threshold for something (unspecified)')
    parser.add_argument('--var_thresh', type=float, default=0.3, help='Variance threshold')

    parser.add_argument('--step_n', type=int, default=4, help='Number of steps')
    parser.add_argument('--run_n', type=int, default=3, help='Number of runs')
    parser.add_argument('--seed', type=int, default=3, help='Seed for randomness')
    parser.add_argument('--device', type=int, default=1, help='Device id')

    parser.add_argument('--refine', type=bool, default=True)
    parser.add_argument('--cosistency', type=bool, default=True)

    return parser.parse_args()


def main():
    args = get_args()
    print(args.sample_dir)
    os.makedirs(args.fig_dir, exist_ok=True)
    os.makedirs(args.save_dir, exist_ok=True)
    device = torch.device(f"cuda:{args.device}")
    set_seed(args.seed)
    record = {}
    dice_avg = []
    ALL = []
    # 
    sample_ds = SampleDataset(args.sample_dir, args.data_dir, args.split)
    transforms = T.Compose([T.ToTensor(), T.Normalize(mean=[.5], std=[.5])])
    # unet model
    evidentialUNet = EvidentialUNet(n_channels=1, n_classes=1)
    evidentialUNet = load_model(evidentialUNet, args.seg_ckpt_dir)
    evidentialUNet.to(device=device)
    evidentialUNet.eval()

    for samples, image, label, sample_id in tqdm(sample_ds):
        # segmentation
        inp = [transforms(sample) for sample in samples]
        inp = torch.stack(inp).to(device)
        masks_pred, v, alpha, beta = evidentialUNet(inp)
        masks_pred = (torch.nn.functional.sigmoid(masks_pred) > 0.5).float() 
        masks_pred = masks_pred.cpu().detach().numpy().squeeze()
        uncertainty_maps = beta / (v * (alpha - 1))
        uncertainty_maps = uncertainty_maps.cpu().detach().numpy().squeeze()

    #     ALL.append([samples, masks_pred, uncertainty_maps, sample_id])

    # for samples, masks_pred, uncertainty_maps, sample_id in tqdm(ALL):
        samples = np.array_split(samples, args.step_n, axis=0)
        masks_pred = np.array_split(masks_pred, args.step_n, axis=0)
        uncertainty_maps = np.array_split(uncertainty_maps, args.step_n, axis=0)

        all_var = np.ones((args.step_n,))
        all_un = np.ones((args.step_n, args.run_n))
        all_dice = np.ones((args.step_n, args.run_n))

        col = args.run_n
        row = args.step_n + 1
        fig, axs = plt.subplots(row, col*3, figsize=(18, 10))
        plot_img(axs[0,0], image)
        plot_img(axs[0,1], label)
        for j in range(3, col*3):
            axs[0, j].set_visible(False)
        axs[0, 2].axis('off')

        # start_time = time.time() 
        for r, (trans, preds, un_maps) in enumerate(zip(samples, masks_pred, uncertainty_maps)):
            var = cal_var(preds)
            all_var[r] = var
            for i in range(trans.shape[0]):
                tran = trans[i]
                pred = preds[i]
                un_map = un_maps[i]
                # un_map = (un_map - un_map.min()) / (un_map.max() - un_map.min())
                un_mask = un_map > args.un_thresh
                # # get image level uncertainty
                un = mask_mean(un_map, un_mask)
                all_un[r, i] = un
                # # process pred using uncertainty mask
                if args.refine:
                    pred = get_new_pred(pred, un_mask)
                dice = cal_dice(im1=pred, im2=label)
                all_dice[r, i] = dice

                ax = axs[r+1, i*3]
                plot_img(ax, tran)
                ax = axs[r+1, i*3+1]
                plot_img(ax, pred, f'dice={dice:.04f}')
                ax = axs[r+1, i*3+2]
                plot_img(ax, un_mask, f'un={un:.06f}')
            axs[r+1, 0].set_title(f'var={var:.04f}')
        # end_time = time.time()
        # print(f"该行运行耗时：{end_time - start_time} 秒")

        best_var, best_pred, best_step, best_run = find_best(all_var, args.var_thresh, masks_pred)
        if best_var is not None:
            best_dice = cal_dice(best_pred, label)
            best_sample = samples[best_step][best_run]
            dice_avg.append(best_dice)

            record[sample_id] = {'best_dice': best_dice, 'best_var': best_var, 'best_step': best_step, 'best_run': best_run}
            sio.savemat(
                f'{args.save_dir}/{sample_id}.mat',
                {
                    'sample': best_sample,
                    'pseudo': best_pred,
                }
            )

            axs[0, 2].text(0, 0.5, f'step = {best_step}\nrun = {best_run}\ndice = {best_dice:.04f}\nvar = {best_var:.04f}')
        plt.savefig(f"{args.fig_dir}/{sample_id}.png", bbox_inches='tight', pad_inches=0.1)
        plt.close()
    
    print(len(dice_avg), sum(dice_avg)/len(dice_avg))

    with open(args.record_save_name, 'w') as f:
        json.dump(record, f, indent=4)


if __name__ == "__main__":
    main()