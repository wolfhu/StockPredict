# Author: Nicolas Boulanger-Lewandowski
# University of Montreal (2012)
# RNN-RBM deep learning tutorial
# More information at http://deeplearning.net/tutorial/rnnrbm.html

import glob
import os
import sys
import time
import pdb
import numpy

import theano
import theano.tensor as T
from theano.tensor.shared_randomstreams import RandomStreams

from RNN_RBM import RnnRbm
sys.path.append('../../tutorial')
from tutorial.LogisticRegression import LogisticRegression
from tutorial.HiddenLayer import HiddenLayer
from tutorial.rbm import RBM


#Don't use a python long as this don't work on 32 bits computers.
numpy.random.seed(0xbeef)
rng = RandomStreams(seed=numpy.random.randint(1 << 30))
theano.config.warn.subtensor_merge_bug = False

class RNNRBM_DBN(object):

    def __init__(self, numpy_rng, theano_rng=None, n_ins=784,
                hidden_layers_sizes=[500,500], hidden_recurrent=150,
                n_outs=1, y_type=1):
            self.sigmoid_layers = []
            self.rnnrbm_layers = []
            self.rbm_layers= []
            self.params = []
            self.n_layers = len(hidden_layers_sizes)

            assert self.n_layers > 0

            if not theano_rng:
                theano_rng = RandomStreams(numpy_rng.randint(2 ** 30))
            # allocate symbolic variables for the data
            self.x = T.matrix('x')  # the data is presented as rasterized images
            if y_type==0:
                self.y = T.matrix('y')  # the labels are presented as 1D vector of
            else:
                self.y = T.ivector('y')  # the labels are presented as 1D vector of
                                     # [int] labels
            # The SdA is an MLP, for which all weights of intermediate layers
            # are shared with a different denoising autoencoders
            # We will first construct the SdA as a deep multilayer perceptron,
            # and when constructing each sigmoidal layer we also construct a
            # denoising autoencoder that shares weights with that layer
            # During pretraining we will train these autoencoders (which will
            # lead to chainging the weights of the MLP as well)
            # During finetunining we will finish training the SdA by doing
            # stochastich gradient descent on the MLP
            
            for i in xrange(self.n_layers):
                # construct the sigmoidal layer
                
                # the size of the input is either the number of hidden units of
                # the layer below or the input size if we are on the first layer
                if i == 0:
                    input_size = n_ins
                else:
                    input_size = hidden_layers_sizes[i - 1]
                
                # the input to this layer is either the activation of the hidden
                # layer below or the input of the SdA if you are on the first
                # layer
                if i == 0:
                    layer_input = self.x
                else:
                    layer_input = self.sigmoid_layers[-1].output
                
                # its arguably a philosophical question...
                # but we are going to only declare that the parameters of the
                # sigmoid_layers are parameters of the StackedDAA
                # the visible biases in the dA are parameters of those
                # dA, but not the SdA
                
                # Construct a denoising autoencoder that shared weights with this
                # layer
                if i== (self.n_layers - 1):
                    rnnrbm_layer = RnnRbm(n_visible=input_size,
                    input=layer_input, n_hidden=hidden_layers_sizes[i],
                    n_hidden_recurrent=hidden_recurrent,lr=0.001,y_type=y_type)
                                        
                    self.rnnrbm_layers.append(rnnrbm_layer)
                    sigmoid_layer = HiddenLayer(rng=numpy_rng,
                                                input=layer_input,
                                                n_in=input_size,
                                                n_out=hidden_layers_sizes[i],
                                                activation=T.nnet.sigmoid,
                                                W=rnnrbm_layer.W, b=rnnrbm_layer.bh_t) 
                else :
                    sigmoid_layer = HiddenLayer(rng=numpy_rng,
                                                input=layer_input,
                                                n_in=input_size,
                                                n_out=hidden_layers_sizes[i],
                                                activation=T.nnet.sigmoid
                                                )

                    rbm_layer = RBM(numpy_rng = numpy_rng,
                                    theano_rng=theano_rng,
                                    input=layer_input,
                                    n_visible=input_size,
                                    n_hidden=hidden_layers_sizes[i],
                                    W=sigmoid_layer.W,
                                    hbias=sigmoid_layer.b,
                                    y_type=y_type)
                    self.rbm_layers.append(rbm_layer) 
                # add the layer to our list of layers
                self.sigmoid_layers.append(sigmoid_layer)
                self.params.extend(sigmoid_layer.params)
            # We now need to add a logistic layer on top of the MLP
            self.logLayer = LogisticRegression(
                             input=self.sigmoid_layers[-1].output,
                             n_in=hidden_layers_sizes[-1], n_out=n_outs, y_type=y_type)
            self.get_prediction = theano.function(
                inputs=[self.x],
                outputs=[self.logLayer.y_pred]
                )
            self.get_py = theano.function(
                inputs=[self.x],
                outputs=[self.logLayer.p_y_given_x]
                )
            self.params.extend(self.logLayer.params)
            # construct a function that implements one step of finetunining

            # compute the cost for second phase of training,
            # defined as the negative log likelihood
            if y_type == 0:
                self.finetune_cost = self.logLayer.squared_error(self.y)
            else :
                self.finetune_cost = self.logLayer.negative_log_likelihood(self.y)
            # compute the gradients with respect to the model parameters
            # symbolic variable that points to the number of errors made on the
            # minibatch given by self.x and self.y
            self.errors = self.logLayer.errors(self.y)


    def pretraining_functions(self,train_set_x,batch_size,k):
        # index to a [mini]batch
        index = T.lscalar('index') # index to a minibatch
        learning_rate = T.scalar('lr')  # learning rate to use

        # number of batches
        n_batches = train_set_x.get_value(borrow=True).shape[0] / batch_size
        # begining of a batch, given `index`
        batch_begin = index * batch_size
        # ending of a batch given `index`
        batch_end = batch_begin + batch_size

        pretrain_fns = []
        pretrain_f_bh_t= []
        for rbm in self.rbm_layers:

            # get the cost and the updates list
            # using CD-k here (persisent=None) for training each RBM.
            # TODO: change cost function to reconstruction error
            cost, updates = rbm.get_cost_updates(learning_rate,
                                                 persistent=None, k=k)

            # compile the theano function
            fn = theano.function(inputs=[index,
                            theano.Param(learning_rate, default=0.1)],
                                 outputs=cost,
                                 updates=updates,
                                 givens={self.x:
                                    train_set_x[batch_begin:batch_end]})
            # append `fn` to the list of functions
            pretrain_fns.append(fn)
        
        for rnnrbm in self.rnnrbm_layers:

            # get the cost and the updates list
            # using CD-k here (persisent=None) for training each RBM.
            # TODO: change cost function to reconstruction error
            cost,updates,bh_t,updates_bh_t = rnnrbm.get_cost_updates(learning_rate)
            # compile the theano function
            fn = theano.function(inputs=[index,
                            theano.Param(learning_rate, default=0.1)],
                                 outputs=cost,
                                 updates=updates,
                                 givens={self.x:
                                    train_set_x[batch_begin:batch_end]})
            # append `fn` to the list of functions
            f_bh_t = theano.function(inputs=[index],
                                 outputs=bh_t,
                                 updates=updates_bh_t,
                                 givens={self.x:
                                    train_set_x[batch_begin:batch_end]})
            pretrain_fns.append(fn)
            pretrain_f_bh_t.append(f_bh_t)


        return pretrain_fns,pretrain_f_bh_t
     

    def build_finetune_functions(self,dataset,batch_size,learning_rate, y_type):

        train_set_x, train_set_y = theano.shared(dataset.phase2['train']['x']), theano.shared(dataset.phase2['train']['y'])
        valid_set_x, valid_set_y = theano.shared(dataset.phase2['valid']['x']), theano.shared(dataset.phase2['valid']['y'])
        test_set_x, test_set_y = theano.shared(dataset.phase2['test']['x']), theano.shared(dataset.phase2['test']['y'])
        if not y_type ==0: 
            train_set_y = T.cast(train_set_y,'int32')
            valid_set_y = T.cast(valid_set_y,'int32')
            test_set_y = T.cast(test_set_y,'int32')
        # compute number of minibatches for training, validation and testing
        n_valid_batches = valid_set_x.get_value(borrow=True).shape[0]
        n_valid_batches /= batch_size
        n_test_batches = test_set_x.get_value(borrow=True).shape[0]
        n_test_batches /= batch_size

        index = T.lscalar('index')  # index to a [mini]batch

        # compute the gradients with respect to the model parameters
        gparams = T.grad(self.finetune_cost, self.params)

        # compute list of fine-tuning updates
        updates = []
        for param, gparam in zip(self.params, gparams):
            updates.append((param, param - gparam * learning_rate))

        train_fn = theano.function(inputs=[index],
              outputs=self.finetune_cost,
              updates=updates,
              givens={self.x: train_set_x[index * batch_size:
                                          (index + 1) * batch_size],
                      self.y: train_set_y[index * batch_size:
                                          (index + 1) * batch_size]},
          name='train')

        test_score_i = theano.function([index], self.errors,
                 givens={self.x: test_set_x[index * batch_size:
                                            (index + 1) * batch_size],
                         self.y: test_set_y[index * batch_size:
                                            (index + 1) * batch_size]},
         name='test')

        valid_score_i = theano.function([index], self.errors,
              givens={self.x: valid_set_x[index * batch_size:
                                          (index + 1) * batch_size],
                      self.y: valid_set_y[index * batch_size:
                                          (index + 1) * batch_size]},
              name='valid')

        # Create a function that scans the entire validation set
        def valid_score():
	    return [valid_score_i(i) for i in xrange(n_valid_batches)]

        # Create a function that scans the entire test set
        def test_score():
            return [test_score_i(i) for i in xrange(n_test_batches)]
        train_set_x, train_set_y, valid_set_x, valid_set_y, test_set_x, test_set_y = "", "", "", "", "", ""

        return train_fn, valid_score, test_score      




def pretrain(pretrain_params, y_type):

    ############################
    ###  Setting parameters  ###
    ############################

    dataset = pretrain_params['dataset']
    hidden_layers_sizes = pretrain_params['hidden_layers_sizes']
    pretrain_lr = pretrain_params['pretrain_lr']
    pretrain_batch_size = pretrain_params['pretrain_batch_size']
    pretrain_epochs = pretrain_params['pretrain_epochs']
    hidden_recurrent = pretrain_params['hidden_recurrent']
    k = pretrain_params['k']
    n_outs = pretrain_params['n_outs']    
    ############################

    train_set_x, train_set_y = theano.shared(dataset.phase2['train']['x']), theano.shared(dataset.phase2['train']['y'])
    valid_set_x, valid_set_y = theano.shared(dataset.phase2['valid']['x']), theano.shared(dataset.phase2['valid']['y'])
    test_set_x, test_set_y = theano.shared(dataset.phase2['test']['x']), theano.shared(dataset.phase2['test']['y'])

    if not y_type==0:
        train_set_y = T.cast(train_set_y,'int32')
        valid_set_y = T.cast(valid_set_y,'int32')
        test_set_y = T.cast(test_set_y,'int32')
    # compute number of minibatches for training, validation and testing
    n_train_batches = train_set_x.get_value(borrow=True).shape[0] / pretrain_batch_size

    # numpy random generator
    numpy_rng = numpy.random.RandomState(123)
    print '... building the model'
    model = ""
    model = RNNRBM_DBN(numpy_rng=numpy_rng, n_ins=train_set_x.get_value().shape[1],
                         hidden_layers_sizes=hidden_layers_sizes,
                         hidden_recurrent=hidden_recurrent,
                         n_outs=n_outs, y_type=y_type)
    
    #########################
    # PRETRAINING THE MODEL #
    #########################
    print '... getting the pretraining functions'
    pretraining_fns,pretraining_f_bh_t = model.pretraining_functions(train_set_x=train_set_x,
                                                batch_size=pretrain_batch_size,k=k)
    print '... pre-training the model'
    start_time = time.clock()
    ## Pre-train layer-wise
    for i in xrange(model.n_layers):
        # go through pretraining epochs
        for epoch in xrange(pretrain_epochs):
            # go through the training set
            c = []
            b = []
            for batch_index in xrange(n_train_batches):
                c.append(pretraining_fns[i](index=batch_index,
                                            lr=pretrain_lr))
                if i==(model.n_layers -1):
                    b.append(pretraining_f_bh_t[0](index=batch_index))
            msg = 'Pre-training layer %i, epoch %d, cost %f' % (i, epoch, numpy.mean(c))
            sys.stdout.write("\r%s" % msg)
            sys.stdout.flush()
        print

    end_time = time.clock()
    print >> sys.stderr, ('The pretraining code for file ' +
                          os.path.split(__file__)[1] +
                          ' ran for %.2fm' % ((end_time - start_time) / 60.))

    train_set_x, train_set_y, valid_set_x, valid_set_y, test_set_x, test_set_y = "", "", "", "", "", ""
    return model

def finetune(finetune_params, y_type):

    ############################
    ###  Setting parameters  ###
    ############################

    dataset = finetune_params['dataset']
    model = finetune_params['model']
    finetune_lr = finetune_params['finetune_lr']
    finetune_batch_size = finetune_params['finetune_batch_size']
    finetune_epochs = finetune_params['finetune_epochs']

    ############################

    train_set_x, train_set_y = theano.shared(dataset.phase2['train']['x']), theano.shared(dataset.phase2['train']['y'])
    valid_set_x, valid_set_y = theano.shared(dataset.phase2['valid']['x']), theano.shared(dataset.phase2['valid']['y'])
    test_set_x, test_set_y = theano.shared(dataset.phase2['test']['x']), theano.shared(dataset.phase2['test']['y']) 
    if not y_type==0 :
        train_set_y = T.cast(train_set_y,'int32')
        valid_set_y = T.cast(valid_set_y,'int32')
        test_set_y = T.cast(test_set_y,'int32')
    ########################
    # FINETUNING THE MODEL #
    ########################

    n_train_batches = train_set_x.get_value(borrow=True).shape[0] / finetune_batch_size
    # get the training, validation and testing function for the model
    print '... getting the finetuning functions'
    train_fn, validate_model, test_model = model.build_finetune_functions(
                dataset=dataset, batch_size=finetune_batch_size,
                learning_rate=finetune_lr, y_type=y_type)

    print '... finetunning the model'
    # early-stopping parameters
    patience = 30 * n_train_batches  # look as this many examples regardless
    patience_increase = 2.    # wait this much longer when a new best is
                              # found
    improvement_threshold = 0.995  # a relative improvement of this much is
                                   # considered significant
    validation_frequency = min(n_train_batches, patience / 2)
                                  # go through this many
                                  # minibatche before checking the network
                                  # on the validation set; in this case we
                                  # check every epoch

    best_params = None
    best_validation_loss = numpy.inf
    best_epoch = 0
    test_score = 0.
    start_time = time.clock()

    done_looping = False
    epoch = 0


    while (epoch < finetune_epochs) and (not done_looping):
        epoch = epoch + 1
        for minibatch_index in xrange(n_train_batches):

            minibatch_avg_cost = train_fn(minibatch_index)
            iter = (epoch - 1) * n_train_batches + minibatch_index
            if (iter + 1) % validation_frequency == 0:

                validation_losses = validate_model()
                this_validation_loss = numpy.mean(validation_losses)
                msg = ('epoch %i, minibatch %i/%i, validation error %f %%' % \
                      (epoch, minibatch_index + 1, n_train_batches,
                       this_validation_loss * 100.))
                sys.stdout.write("\r%s" % msg)
                sys.stdout.flush()# if we got the best validation score until now
                # if we got the best validation score until now
                if this_validation_loss < best_validation_loss:
                    best_epoch = epoch
                    #improve patience if loss improvement is good enough
                    if (this_validation_loss < best_validation_loss *
                        improvement_threshold):
                        patience = max(patience, iter * patience_increase)

                    # save best validation score and iteration number
                    best_validation_loss = this_validation_loss
                    best_iter = iter

                    # test it on the test set
                    test_losses = test_model()
                    test_score = numpy.mean(test_losses)
                    print(('     epoch %i, minibatch %i/%i, test error of '
                           'best model %f %%') %
                          (epoch, minibatch_index + 1, n_train_batches,
                           test_score * 100.))

            if patience <= iter:
                done_looping = True
                break

    print test_score
    #pdb.set_trace()

    train_set_x, train_set_y, valid_set_x, valid_set_y, test_set_x, test_set_y = "", "", "", "", "", ""    
    return model, best_validation_loss, test_score, best_epoch

if __name__ == '__main__':
    pass
