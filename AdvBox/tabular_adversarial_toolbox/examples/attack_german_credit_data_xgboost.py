import pdb
import sys
sys.path.append('../')
import argparse

import numpy as np
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import MinMaxScaler
import xgboost as xgb

from tabular_adversarial.datasets.german_credit_dataset import load_gcd
from tabular_adversarial.utils.data_utils import get_onehot_encoders, raw_to_onehot, onehot_to_raw, get_label_encoders, raw_to_label, label_to_raw, DataProcessor, DataCorrector, vector_transform_by_onehot_info
from tabular_adversarial.utils.misc_utils import set_seed
from tabular_adversarial.predictors.classification.xgboost import XGBoostClassifier
from tabular_adversarial.attacks.zoo import ZooAttack
from tabular_adversarial.losses.norm_function import CheckAndImportanceNorm
from tabular_adversarial.losses.loss_function import CWLoss

def parse_args():
    '''
    Parse the arguments.
 
    Returns:
        A parser.
    '''

    parser = argparse.ArgumentParser(
        description='Train a XGBoost on the German Credit Dataset, then generates adversarial examples using ZOO.'
    )

    parser.add_argument(
        '--data_path',
        help='Path of file "german.data" in German Credit Dataset.',
        required=True,
        type=str,
    )

    '''
    parser.add_argument(
        '--model_path',
        help='Path of trained XGBoost by this script, if None, train a new XGBoost.',
        default=None,
        type=str
    ) 
    '''

    parser.add_argument(
        '--seed',
        help='Random seed, default: 666',
        default=666,
        type=int
    )

    return parser.parse_args()

def train_xgboost(X_train, Y_train, params, num_round=10):
    '''
    Training a XGBoost model useing data and labels.
    
    Args:
        X_train (numpy.ndarray): Data use to train XGBoost.
        Y_train (numpy.ndarray): Labels use to train XGBoost.
        params (dict):  Parameters of XGBoost model.
        num_round (int): The number of iterations of boosting.

    Returns:
        xgb_model: Trained XGBoost model.
    '''

    train_data = xgb.DMatrix(X_train, label=Y_train)
    xgb_model = xgb.train(params, train_data, num_round)

    return xgb_model
    

def main():
    '''
    Main function to loading the German Credit Dataset, training XGBoost and generates adversarial examples using ZOO.
    '''

    # Parse parameters
    args = parse_args()

    # Set random seed
    set_seed(args.seed)
    
    # Loading German Credit Dataset
    file_path = args.data_path
    gcd_X, gcd_Y = load_gcd(file_path)

    # Get list of onehot_encoders by raw data. You can also set your own.
    X_onehot_encoders_list = get_onehot_encoders(gcd_X)

    # Get list of label_encoders by raw data. You can also set your own.
    Y_label_encoders_list = get_label_encoders(gcd_Y.reshape(-1, 1), deal_type=type(gcd_Y[0]))

    # Split datasets
    # Split train dataset and test dataset use all data
    X_train, X_test, Y_train, Y_test = train_test_split(gcd_X, gcd_Y, test_size=0.3, stratify=gcd_Y)
    # Split train dataset and val dataset use train dataset
    X_train, X_val, Y_train, Y_val = train_test_split(X_train, Y_train, test_size=0.2, stratify=Y_train)

    # Transform raw train X to onehot data
    X_train_onehot = raw_to_onehot(X_train, X_onehot_encoders_list)

    # Transform raw train Y to label embedding by label_encoders.
    Y_train_label = raw_to_label(Y_train.reshape(-1, 1), Y_label_encoders_list)

    # Training XGBoost
    num_classes = 2
    num_features = X_train_onehot.shape[1]
    params = {'objective': 'multi:softprob', 'num_class': num_classes}
    model = train_xgboost(X_train_onehot, Y_train_label, params, num_round=10)
    
    # Building a classifier
    # Building preprocessing function
    preprocessing_func = lambda x: raw_to_onehot(x, X_onehot_encoders_list)
    # Building a classifier
    classifier = XGBoostClassifier(model, preprocessing=preprocessing_func, postprocessing=None, nb_features=num_features, nb_classes=num_classes)

    # Building a attacker.

    # Building a function (class) of calculate the distortion norm.
    # Assume a field checkability vector and a field importance vector. Assume for the moment that `CheckAndImportanceNorm` is equivalent to p-norm.
    field_check = [0] * num_features
    field_importance = [1] * num_features
    # Transfrom field-level vector to feature-level vector
    feature_check = vector_transform_by_onehot_info(field_check, X_onehot_encoders_list)
    feature_importance = vector_transform_by_onehot_info(field_importance, X_onehot_encoders_list)
    # Building norm function.
    norm_func = CheckAndImportanceNorm(check_vector=feature_check, importance_vector=feature_importance, alpha=1, beta=1, norm_type='l2')

    # Building a function (class) of calculate the adversarial loss.
    loss_func = CWLoss()

    # Build a processor.
    # Build a scaler. This is built for convenience using training data. You can also use custom value ranges to generate virtual samples for building.
    scaler = MinMaxScaler()
    scaler.fit(X_train_onehot)
    
    # Note that the onehot_encoders_list used here doesn't have to be the same as the model training, it's just for convenience.
    processor = DataProcessor(X_onehot_encoders_list, scaler=scaler) 

    # Build a corrector, if data correction is required.
    # Fill in the field type for each field
    field_types_list = [
        'Onehot',
        'Positive Integer',
        'Onehot',
        'Onehot',
        'Positive Integer', #
        'Onehot',
        'Onehot',
        'Positive Integer',
        'Onehot',
        'Onehot', #
        'Integer more 1',
        'Onehot',
        'Positive Integer',
        'Onehot',
        'Onehot',
        'Positive Integer',
        'Onehot',
        'Positive Integer',
        'Onehot',
        'Onehot',
    ]
    # Note that the onehot_encoders_list used here doesn't have to be the same as the model training, it's just for convenience. But it has to be the same as the processor.
    corrector = DataCorrector(field_types_list, X_onehot_encoders_list)

    attacker = ZooAttack(
        predictor=classifier, 
        norm_func=norm_func, 
        loss_func=loss_func,
        targeted=False, 
        learning_rate=1.0, 
        max_iter=100,
        const_binary_search_steps=1, 
        initial_const=1.0, 
        allowed_vector=None,
        nb_parallel=10,
        variable_h=0.1, 
        processor=processor,
        corrector=corrector
    )
        

    for data in X_test:
        data = data.reshape(1, -1)
        ori_label = np.argmax(classifier.predict(data))
        if ori_label == 1:
            o_best_distortion_norms, o_best_adversarial_losses, o_best_labels, o_best_attacks, o_success_indices = attacker.generate(data)
            print(o_success_indices, o_best_labels)
            break
        
if __name__ == '__main__':
    main()
