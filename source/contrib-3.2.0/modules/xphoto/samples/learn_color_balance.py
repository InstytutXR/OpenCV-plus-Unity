#!/usr/bin/env python
from __future__ import print_function
import os, sys, argparse
import numpy as np
import scipy.io
from sklearn.tree import DecisionTreeRegressor
import cv2
import random


def parse_sequence(input_str):
    if len(input_str) == 0:
        return []
    else:
        return [o.strip() for o in input_str.split(",") if o]


def convert_to_8bit(arr, clip_percentile = 2.5):
    arr = np.clip(arr * (255.0 / np.percentile(arr, 100 - clip_percentile)), 0, 255)
    return arr.astype(np.uint8)


def learn_regression_tree_ensemble(img_features, gt_illuminants, num_trees, max_tree_depth):
    eps = 0.001
    inst = [[img_features[i], gt_illuminants[i][0] / (sum(gt_illuminants[i]) + eps),
                              gt_illuminants[i][1] / (sum(gt_illuminants[i]) + eps)] for i in range(len(img_features))]

    inst.sort(key = lambda obj: obj[1]) #sort by r chromaticity
    stride = int(np.ceil(len(inst) / float(num_trees+1)))
    sz = 2*stride
    dst_model = []
    for tree_idx in range(num_trees):
        #local group in the training data is additionally weighted by num_trees
        local_group_range = range(tree_idx*stride, min(tree_idx*stride+sz, len(inst)))
        X = num_trees * [inst[i][0] for i in local_group_range]
        y_r = num_trees * [inst[i][1] for i in local_group_range]
        y_g = num_trees * [inst[i][2] for i in local_group_range]

        #add the rest of the training data:
        X = X + [inst[i][0] for i in range(len(inst)) if i not in local_group_range]
        y_r = y_r + [inst[i][1] for i in range(len(inst)) if i not in local_group_range]
        y_g = y_g + [inst[i][2] for i in range(len(inst)) if i not in local_group_range]

        local_model = []
        for feature_idx in range(len(X[0])):
            tree_r = DecisionTreeRegressor(max_depth = max_tree_depth, random_state = 1234)
            tree_r.fit([el[feature_idx][0] for el in X], y_r)
            tree_g = DecisionTreeRegressor(max_depth = max_tree_depth, random_state = 1234)
            tree_g.fit([el[feature_idx][0] for el in X], y_g)
            local_model.append([tree_r, tree_g])
        dst_model.append(local_model)
    return dst_model


def get_tree_node_lists(tree, tree_depth):
    dst_feature_idx = (2**tree_depth-1) * [0]
    dst_thresh_vals = (2**tree_depth-1) * [.5]
    dst_leaf_vals   = (2**tree_depth) * [-1]
    leaf_idx_offset = (2**tree_depth-1)
    left      = tree.tree_.children_left
    right     = tree.tree_.children_right
    threshold = tree.tree_.threshold
    value = tree.tree_.value
    feature = tree.tree_.feature

    def recurse(left, right, threshold, feature, node, dst_idx, cur_depth):
        if (threshold[node] != -2):
            dst_feature_idx[dst_idx] = feature[node]
            dst_thresh_vals[dst_idx] = threshold[node]
            if left[node] != -1:
                recurse (left, right, threshold, feature, left[node], 2*dst_idx+1, cur_depth + 1)
            if right[node] != -1:
                recurse (left, right, threshold, feature, right[node], 2*dst_idx+2, cur_depth + 1)
        else:
            range_start = 2**(tree_depth - cur_depth) * dst_idx + (2**(tree_depth - cur_depth) - 1) - leaf_idx_offset
            range_end = 2**(tree_depth - cur_depth) * dst_idx + (2**(tree_depth - cur_depth+1) - 2) - leaf_idx_offset + 1
            dst_leaf_vals[range_start:range_end] = (range_end - range_start) * [value[node][0][0]]

    recurse(left, right, threshold, feature, 0, 0, 0)
    return (dst_feature_idx, dst_thresh_vals, dst_leaf_vals)


def generate_code(model, input_params, use_YML, out_file):
    feature_idx = []
    thresh_vals = []
    leaf_vals = []
    depth = int(input_params["--max_tree_depth"])
    for local_model in model:
        for feature in local_model:
            (local_feature_idx, local_thresh_vals, local_leaf_vals) = get_tree_node_lists(feature[0], depth)
            feature_idx += local_feature_idx
            thresh_vals += local_thresh_vals
            leaf_vals += local_leaf_vals
            (local_feature_idx, local_thresh_vals, local_leaf_vals) = get_tree_node_lists(feature[1], depth)
            feature_idx += local_feature_idx
            thresh_vals += local_thresh_vals
            leaf_vals += local_leaf_vals
    if use_YML:
        fs = cv2.FileStorage(out_file, 1)
        fs.write("num_trees", len(model))
        fs.write("num_tree_nodes", 2**depth)
        fs.write("feature_idx", np.array(feature_idx).astype(np.uint8))
        fs.write("thresh_vals", np.array(thresh_vals).astype(np.float32))
        fs.write("leaf_vals", np.array(leaf_vals).astype(np.float32))
        fs.release()
    else:
        res = "/* This file was automatically generated by learn_color_balance.py script\n" +\
              " * using the following parameters:\n"
        for key in input_params:
            res += " " + key + " " + input_params[key]
        res += "\n */\n"
        res += "const int num_features = 4;\n"
        res += "const int _num_trees = " + str(len(model)) + ";\n"
        res += "const int _num_tree_nodes = " + str(2**depth) + ";\n"

        res += "unsigned char _feature_idx[_num_trees*num_features*2*(_num_tree_nodes-1)] = {" + str(feature_idx[0])
        for i in range(1,len(feature_idx)):
            res += "," + str(feature_idx[i])
        res += "};\n"

        res += "float _thresh_vals[_num_trees*num_features*2*(_num_tree_nodes-1)] = {" + ("%.3ff" % thresh_vals[0])[1:]
        for i in range(1,len(thresh_vals)):
            res += "," + ("%.3ff" % thresh_vals[i])[1:]
        res += "};\n"

        res += "float _leaf_vals[_num_trees*num_features*2*_num_tree_nodes] = {" + ("%.3ff" % leaf_vals[0])[1:]
        for i in range(1,len(leaf_vals)):
            res += "," + ("%.3ff" % leaf_vals[i])[1:]
        res += "};\n"
        f = open(out_file,"w")
        f.write(res)
        f.close()


def load_ground_truth(gt_path):
    gt = scipy.io.loadmat(gt_path)
    base_gt_illuminants = []
    black_levels = []
    if "groundtruth_illuminants" in gt.keys() and "darkness_level" in gt.keys():
        #NUS 8-camera dataset format
        base_gt_illuminants = gt["groundtruth_illuminants"]
        black_levels = len(base_gt_illuminants) * [gt["darkness_level"][0][0]]
    elif "real_rgb" in gt.keys():
        #Gehler-Shi dataset format
        base_gt_illuminants = gt["real_rgb"]
        black_levels = 87 * [0] + (len(base_gt_illuminants) - 87) * [129]
    else:
        print("Error: unknown ground-truth format, only formats of Gehler-Shi and NUS 8-camera datasets are supported")
        sys.exit(1)

    return (base_gt_illuminants, black_levels)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description=("A tool for training the learning-based "
                     "color balance algorithm. Currently supports "
                     "training only on the Gehler-Shi and NUS 8-camera datasets."),
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "-i",
        "--input_folder",
        metavar="INPUT_FOLDER",
        default="",
        help=("Folder containing the training dataset. Assumes minimally "
              "processed png images like in the Gehler-Shi (http://www.cs.sfu.ca/~colour/data/shi_gehler/) "
              "or NUS 8-camera (http://www.comp.nus.edu.sg/~whitebal/illuminant/illuminant.html) datasets"))
    parser.add_argument(
        "-g",
        "--ground_truth",
        metavar="GROUND_TRUTH",
        default="real_illum_568..mat",
        help=("Path to the mat file containing ground truth illuminations. Currently "
              "supports formats supplied by the Gehler-Shi and NUS 8-camera datasets."))
    parser.add_argument(
        "-r",
        "--range",
        metavar="RANGE",
        default="0,0",
        help="Range of images from the input dataset to use for training")
    parser.add_argument(
        "-o",
        "--out",
        metavar="OUT",
        default="color_balance_model.yml",
        help="Path to the output learnt model. Either a .yml (for loading during runtime) "
             "or .hpp (for compiling with the main code) file ")
    parser.add_argument(
        "--hist_bin_num",
        metavar="HIST_BIN_NUM",
        default="64",
        help=("Size of one dimension of a three-dimensional RGB histogram employed in the "
              "feature extraction step."))
    parser.add_argument(
        "--num_trees",
        metavar="NUM_TREES",
        default="20",
        help=("Parameter to control the size of the regression tree ensemble"))
    parser.add_argument(
        "--max_tree_depth",
        metavar="MAX_TREE_DEPTH",
        default="4",
        help=("Maxmimum depth of regression trees constructed during training."))
    parser.add_argument(
        "-a",
        "--num_augmented",
        metavar="NUM_AUGMENTED",
        default="2",
        help=("Number of augmented samples per one training image. Training set "
              "augmentation tends to improve the learnt model robustness."))

    args, other_args = parser.parse_known_args()

    if not os.path.exists(args.input_folder):
        print("Error: " + args.input_folder + (" does not exist. Please, correctly "
                                                 "specify the -i parameter"))
        sys.exit(1)

    if not os.path.exists(args.ground_truth):
        print("Error: " + args.ground_truth + (" does not exist. Please, correctly "
                                                 "specify the -g parameter"))
        sys.exit(1)

    img_range = map(int,parse_sequence(args.range))
    if len(img_range)!=2:
        print("Error: Please specify the -r parameter in form <first_image_index>,<last_image_index>")
        sys.exit(1)

    use_YML = None
    if args.out.endswith(".yml"):
        use_YML = True
    elif args.out.endswith(".hpp"):
        use_YML = False
    else:
        print("Error: Only .hpp and .yml are supported as output formats")
        sys.exit(1)

    hist_bin_num = int(args.hist_bin_num)
    num_trees = int(args.num_trees)
    max_tree_depth = int(args.max_tree_depth)
    img_files = sorted(os.listdir(args.input_folder))
    (base_gt_illuminants,black_levels) = load_ground_truth(args.ground_truth)

    features = []
    gt_illuminants = []
    i=0
    sz = len(img_files)
    random.seed(1234)
    inst = cv2.xphoto.createLearningBasedWB()
    inst.setRangeMaxVal(255)
    inst.setSaturationThreshold(0.98)
    inst.setHistBinNum(hist_bin_num)
    for file in img_files:
        if (i>=img_range[0] and i<img_range[1]) or (img_range[0]==img_range[1]==0):
            cur_path = os.path.join(args.input_folder,file)
            im = cv2.imread(cur_path, -1).astype(np.float32)
            im -= black_levels[i]
            im_8bit = convert_to_8bit(im)
            cur_img_features = inst.extractSimpleFeatures(im_8bit, None)
            features.append(cur_img_features.tolist())
            gt_illuminants.append(base_gt_illuminants[i].tolist())

            for iter in range(int(args.num_augmented)):
                R_coef = random.uniform(0.2, 5.0)
                G_coef = random.uniform(0.2, 5.0)
                B_coef = random.uniform(0.2, 5.0)
                im_8bit = im
                im_8bit[:,:,0] *= B_coef
                im_8bit[:,:,1] *= G_coef
                im_8bit[:,:,2] *= R_coef
                im_8bit = convert_to_8bit(im)
                cur_img_features = inst.extractSimpleFeatures(im_8bit, None)
                features.append(cur_img_features.tolist())
                illum = base_gt_illuminants[i]
                illum[0] *= R_coef
                illum[1] *= G_coef
                illum[2] *= B_coef
                gt_illuminants.append(illum.tolist())

            sys.stdout.write("Computing features: [%3d/%3d]\r" % (i, sz)),
            sys.stdout.flush()
        i+=1

    print("\nLearning the model...")
    model = learn_regression_tree_ensemble(features, gt_illuminants, num_trees, max_tree_depth)
    print("Writing the model...")
    generate_code(model,{"-r":args.range, "--hist_bin_num": args.hist_bin_num, "--num_trees": args.num_trees,
                         "--max_tree_depth": args.max_tree_depth, "--num_augmented": args.num_augmented},
                  use_YML, args.out)
    print("Done")
