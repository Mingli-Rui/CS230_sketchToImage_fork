import time
import datetime
import pytz 
import argparse

import numpy as np
import torch 
import torch.nn as nn
import torch.nn.functional as F
import torchvision.utils as vutils 


from model.net import BasicModel, DomainAdversarialNet
from model.dataloader import Dataloaders
from model.layers import grad_reverse
from evaluate import evaluate
from utils import *

class Trainer():
  def __init__(self, data_dir):
    self.dataloaders = Dataloaders(data_dir)
    self.train_dict = self.dataloaders.train_dict
    self.test_dict = self.dataloaders.test_dict
  
  #def train_and_evaluate(self, config, checkpoint=None):
  def train_and_evaluate(self, checkpoint=None):
    #batch_size = config['batch_size']
    batch_size = 32
    device = torch.device('cuda') if torch.cuda.is_available() else torch.device('cpu')

    train_dataloader = self.dataloaders.get_train_dataloader(batch_size = batch_size, shuffle=True) 
    num_batches = len(train_dataloader) 

    image_model = BasicModel().to(device)
    sketch_model = BasicModel().to(device) 
    
    domain_net = DomainAdversarialNet().to(device)    

    params = [param for param in image_model.parameters() if param.requires_grad == True]
    params.extend([param for param in sketch_model.parameters() if param.requires_grad == True]) 
    params.extend([param for param in domain_net.parameters() if param.requires_grad == True]) 
    #optimizer = torch.optim.Adam(params, lr=config['lr'])
    optimizer = torch.optim.Adam(params, 0.001)

    criterion = nn.TripletMarginLoss(margin = 1.0, p = 2)
    domain_criterion = nn.BCELoss()

    if checkpoint:
      load_checkpoint(checkpoint, image_model, sketch_model, domain_net, optimizer)

    print('Training...')    
    

    #for epoch in range(config['epochs']):
    for epoch in range(10):
      accumulated_triplet_loss = RunningAverage()
      accumulated_iteration_time = RunningAverage()
      accumulated_image_domain_loss = RunningAverage()
      accumulated_sketch_domain_loss = RunningAverage()

      epoch_start_time = time.time()

      image_model.train() 
      sketch_model.train()
      domain_net.train() 
      
      for iteration, batch in enumerate(train_dataloader):
        time_start = time.time()        

        '''GETTING THE DATA'''
        anchors, positives, negatives, label_embeddings, positive_label_idxs, negative_label_idxs = batch
        anchors = torch.autograd.Variable(anchors.to(device)); positives = torch.autograd.Variable(positives.to(device))
        negatives = torch.autograd.Variable(negatives.to(device)); label_embeddings = torch.autograd.Variable(label_embeddings.to(device))

        '''MAIN NET INFERENCE AND LOSS'''
        pred_sketch_features = sketch_model(anchors)
        pred_positives_features = image_model(positives)
        pred_negatives_features = image_model(negatives)

        #triplet_loss = config['triplet_loss_ratio'] * criterion(pred_sketch_features, pred_positives_features, pred_negatives_features)
        triplet_loss = 1 * criterion(pred_sketch_features, pred_positives_features, pred_negatives_features)
        accumulated_triplet_loss.update(triplet_loss, anchors.shape[0])        

        '''DOMAIN ADVERSARIAL TRAINING''' # vannila generator for now. Later - add randomness in outputs of generator, or lower the label

        '''DEFINE TARGETS'''
          
        image_domain_targets = torch.full((anchors.shape[0],1), 1, dtype=torch.float, device=device)
        sketch_domain_targets = torch.full((anchors.shape[0],1), 0, dtype=torch.float, device=device)
          
        '''GET DOMAIN NET PREDICTIONS FOR INPUTS WITH G.R.L.'''
        if epoch < 5:
          grl_weight = 0
        #elif epoch < config['grl_threshold_epoch']:
          #grl_weight *= epoch/config['grl_threshold_epoch'] 
        elif epoch < 25:
           grl_weight *= epoch/25
        else:
          grl_weight = 1

        domain_pred_p_images = domain_net(grad_reverse(pred_positives_features, grl_weight))
        domain_pred_n_images = domain_net(grad_reverse(pred_negatives_features, grl_weight))
        domain_pred_sketches = domain_net(grad_reverse(pred_sketch_features, grl_weight))

        '''DOMAIN LOSS'''

        #domain_loss_images = config['domain_loss_ratio'] * (domain_criterion(domain_pred_p_images, image_domain_targets) + domain_criterion(domain_pred_n_images, image_domain_targets))
        domain_loss_images = 0.5 * (domain_criterion(domain_pred_p_images, image_domain_targets) + domain_criterion(domain_pred_n_images, image_domain_targets))
        accumulated_image_domain_loss.update(domain_loss_images, anchors.shape[0])
        #domain_loss_sketches = config['domain_loss_ratio'] * (domain_criterion(domain_pred_sketches, sketch_domain_targets))
        domain_loss_sketches = 0.5 * (domain_criterion(domain_pred_sketches, sketch_domain_targets))
        accumulated_sketch_domain_loss.update(domain_loss_sketches, anchors.shape[0])    
        total_domain_loss = domain_loss_images + domain_loss_sketches

        '''OPTIMIZATION W.R.T. BOTH LOSSES'''
        optimizer.zero_grad()  
        total_loss = triplet_loss + total_domain_loss
        total_loss.backward()
        optimizer.step()  


        '''LOGGER'''
        time_end = time.time()
        accumulated_iteration_time.update(time_end - time_start)

        #if iteration % config['print_every'] == 0:
        if iteration % 10 == 0:
          eta_cur_epoch = str(datetime.timedelta(seconds = int(accumulated_iteration_time() * (num_batches - iteration)))) 
          print(datetime.datetime.now(pytz.timezone('America/Los_Angeles')).replace(microsecond = 0), end = ' ')

          print('Epoch: %d [%d / %d] ; eta: %s' % (epoch, iteration, num_batches, eta_cur_epoch))
          print('Average Triplet loss: %f(%f);' % (triplet_loss, accumulated_triplet_loss()))
          print('Sketch domain loss: %f; Image Domain loss: %f' % (accumulated_sketch_domain_loss(), accumulated_image_domain_loss()))
        
      '''END OF EPOCH'''
      epoch_end_time = time.time()
      print('Epoch %d complete, time taken: %s' % (epoch, str(datetime.timedelta(seconds = int(epoch_end_time - epoch_start_time)))))
      torch.cuda.empty_cache()

      save_checkpoint({'iteration': iteration + epoch * num_batches, 
                        'image_model': image_model.state_dict(), 
                        'sketch_model': sketch_model.state_dict(),
                        'domain_model': domain_net.state_dict(),
                        'optim_dict': optimizer.state_dict()},
                         #checkpoint_dir = config['checkpoint_dir'])
                         checkpoint_dir = "C:/Users/rub/Desktop/Stanford/CS230/Project/Zero-Shot-Sketch-Based-Image-Retrieval-master/Zero-Shot-Sketch-Based-Image-Retrieval-master/saveModel")
      print('Saved epoch!')
      print('\n\n\n')


if __name__ == '__main__':
  '''
  parser = argparse.ArgumentParser(description='Training of SBIR')
  parser.add_argument('--data_dir', help='Data directory path. Directory should contain two folders - sketches and photos, along with 2 .txt files for the labels', required = True)
  parser.add_argument('--batch_size', type=int, help='Batch size to process the train sketches/photos', required = True)
  parser.add_argument('--checkpoint_dir', help='Directory to save checkpoints', required=True)
  parser.add_argument('--epochs', help='Number of epochs', required=True)

  parser.add_argument('--domain_loss_ratio', help='Domain loss weight', default = 0.5)
  parser.add_argument('--triplet_loss_ratio', help='Triplet loss weight', default = 1.0)
  parser.add_argument('--grl_threshold_epoch', help='Threshold epoch for GRL lambda', default = 25)
  parser.add_argument('--print_every', help='Logging interval in iterations', default = 10)

  args = parser.parse_args()'''
  data_dir = "C:/Users/rub/Desktop/Stanford/CS230/Project/Zero-Shot-Sketch-Based-Image-Retrieval-master/Zero-Shot-Sketch-Based-Image-Retrieval-master/Dataset/"
  #trainer = Trainer(args.data_dir)
  trainer = Trainer(data_dir)
  #trainer.train_and_evaluate(vars(args))
  trainer.train_and_evaluate()